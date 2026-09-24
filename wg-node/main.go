package main

import (
	"bytes"
	"context"
	"crypto/aes"
	"crypto/cipher"
	"crypto/rand"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

type config struct {
	PanelURL   string
	NodeID     string
	APIKey     string
	PublicHost string
	Port       string
	Interface  string
	Pool       string
	DNS        string
	StatePath  string
}

type command struct {
	ID        string          `json:"id"`
	Operation string          `json:"operation"`
	Payload   json.RawMessage `json:"payload"`
}

type peerCreate struct {
	PeerID           string `json:"peer_id"`
	InterfaceName    string `json:"interface_name"`
	ClientIP         string `json:"client_ip"`
	Endpoint         string `json:"endpoint"`
	ServerPublicKey  string `json:"server_public_key"`
	DNS              string `json:"dns"`
	AllowedIPs       string `json:"allowed_ips"`
	PreviousPublicKey string `json:"previous_public_key"`
	ForceRecreate    bool   `json:"force_recreate"`
}

type peerState struct {
	PublicKey string `json:"public_key"`
	Config    string `json:"config"`
	Interface string `json:"interface_name"`
	ClientIP  string `json:"client_ip"`
}

type daemon struct {
	cfg    config
	client *http.Client
	state  map[string]peerState
}

func loadConfig() (config, error) {
	c := config{
		PanelURL: strings.TrimRight(os.Getenv("WG_PANEL_URL"), "/"),
		NodeID: os.Getenv("WG_NODE_ID"), APIKey: os.Getenv("WG_SERVER_API_KEY"),
		PublicHost: os.Getenv("WG_PUBLIC_HOST"), Port: env("WG_SERVER_PORT", "51820"),
		Interface: env("WG_INTERFACE", "wg0"),
		Pool: env("WG_CLIENT_POOL", "10.44.0.0/24"), DNS: env("WG_DNS", "1.1.1.1"),
		StatePath: env("WG_STATE_PATH", "/var/lib/wg-node/peers.json.enc"),
	}
	u, err := url.Parse(c.PanelURL)
	if err != nil || u.Scheme != "https" || u.Host == "" || c.NodeID == "" || len(c.APIKey) < 32 || c.PublicHost == "" {
		return c, errors.New("WG_PANEL_URL (HTTPS), WG_NODE_ID, WG_SERVER_API_KEY and WG_PUBLIC_HOST are required")
	}
	if _, err := strconv.Atoi(c.Port); err != nil {
		return c, errors.New("WG_SERVER_PORT must be numeric")
	}
	return c, nil
}

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" { return value }
	return fallback
}

func newDaemon(c config) (*daemon, error) {
	d := &daemon{cfg: c, client: &http.Client{Timeout: 35 * time.Second}, state: map[string]peerState{}}
	if err := d.loadState(); err != nil { return nil, err }
	return d, nil
}

func (d *daemon) endpoint() string { return d.cfg.PublicHost + ":" + d.cfg.Port }

func (d *daemon) request(ctx context.Context, method, path string, body any, out any) error {
	var reader io.Reader
	if body != nil {
		data, err := json.Marshal(body); if err != nil { return err }; reader = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, d.cfg.PanelURL+path, reader)
	if err != nil { return err }
	req.Header.Set("X-Node-Key", d.cfg.APIKey)
	if body != nil { req.Header.Set("Content-Type", "application/json") }
	resp, err := d.client.Do(req); if err != nil { return err }; defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		message, _ := io.ReadAll(io.LimitReader(resp.Body, 2048))
		return fmt.Errorf("panel returned %s: %s", resp.Status, strings.TrimSpace(string(message)))
	}
	if out == nil { _, err = io.Copy(io.Discard, resp.Body); return err }
	var envelope struct { Success bool `json:"success"`; Data json.RawMessage `json:"data"` }
	if err := json.NewDecoder(resp.Body).Decode(&envelope); err != nil { return err }
	if !envelope.Success { return errors.New("panel returned an unsuccessful response") }
	return json.Unmarshal(envelope.Data, out)
}

func (d *daemon) register(ctx context.Context) error {
	publicKey, err := wg("show", d.cfg.Interface, "public-key"); if err != nil { return err }
	var result map[string]any
	return d.request(ctx, http.MethodPost, "/api/v1/node-agent/"+url.PathEscape(d.cfg.NodeID)+"/register", map[string]string{
		"interface_name": d.cfg.Interface, "endpoint": d.endpoint(), "public_key": publicKey,
		"address_pool": d.cfg.Pool, "dns": d.cfg.DNS,
	}, &result)
}

func (d *daemon) run(ctx context.Context) error {
	for {
		if err := d.register(ctx); err != nil {
			log.Printf("registration failed: %v", err)
			if !sleep(ctx, 3*time.Second) { return nil }
			continue
		}
		log.Printf("connected to panel as node %s", d.cfg.NodeID)
		if err := d.restorePeers(); err != nil {
			log.Printf("peer restore failed: %v", err)
		}
		for ctx.Err() == nil {
			var item *command
			path := "/api/v1/node-agent/"+url.PathEscape(d.cfg.NodeID)+"/commands?wait_seconds=0"
			if err := d.request(ctx, http.MethodGet, path, nil, &item); err != nil {
				log.Printf("panel connection lost: %v", err)
				break
			}
			if item == nil {
				if !sleep(ctx, 2*time.Second) { return nil }
				continue
			}
			result, runErr := d.execute(*item)
			payload := map[string]any{"result": result, "error": ""}
			if runErr != nil { payload["error"] = runErr.Error() }
			if err := d.request(ctx, http.MethodPost, "/api/v1/node-agent/"+url.PathEscape(d.cfg.NodeID)+"/commands/"+url.PathEscape(item.ID)+"/result", payload, nil); err != nil {
				log.Printf("command result upload failed: %v", err)
				break
			}
		}
		if !sleep(ctx, 3*time.Second) { return nil }
	}
}

func (d *daemon) execute(item command) (map[string]any, error) {
	var payload map[string]any
	if err := json.Unmarshal(item.Payload, &payload); err != nil { return nil, err }
	switch item.Operation {
	case "ping":
		iface := stringValue(payload["interface_name"], d.cfg.Interface)
		if _, err := wg("show", iface); err != nil { return nil, err }
		return map[string]any{"status": "ok", "interface": iface}, nil
	case "peer.create":
		var input peerCreate
		if err := json.Unmarshal(item.Payload, &input); err != nil { return nil, err }
		return d.createPeer(input)
	case "peer.delete":
		return d.deletePeer(stringValue(payload["peer_id"], ""))
	case "traffic":
		return d.traffic(stringValue(payload["interface_name"], d.cfg.Interface))
	default:
		return nil, fmt.Errorf("unsupported command %q", item.Operation)
	}
}

func (d *daemon) createPeer(input peerCreate) (map[string]any, error) {
	if input.PeerID == "" { return nil, errors.New("peer_id is required") }
	iface := input.InterfaceName; if iface == "" { iface = d.cfg.Interface }
	if old, ok := d.state[input.PeerID]; ok && !input.ForceRecreate {
		if err := wgRun("set", old.Interface, "peer", old.PublicKey, "allowed-ips", hostRoute(input.ClientIP)); err != nil { return nil, err }
		return map[string]any{"public_key": old.PublicKey, "config": old.Config}, nil
	}
	if old, ok := d.state[input.PeerID]; ok {
		if err := wgRun("set", old.Interface, "peer", old.PublicKey, "remove"); err != nil { log.Printf("could not remove old peer key: %v", err) }
		delete(d.state, input.PeerID)
	}
	privateKey, err := wg("genkey"); if err != nil { return nil, err }
	publicKey, err := wgInput(privateKey, "pubkey"); if err != nil { return nil, err }
	if _, err := wg("show", iface); err != nil { return nil, err }
	if err := wgRun("set", iface, "peer", publicKey, "allowed-ips", hostRoute(input.ClientIP)); err != nil { return nil, err }
	if input.PreviousPublicKey != "" && input.PreviousPublicKey != publicKey {
		_ = wgRun("set", iface, "peer", input.PreviousPublicKey, "remove")
	}
	dns := input.DNS; if dns == "" { dns = d.cfg.DNS }
	allowed := input.AllowedIPs; if allowed == "" { allowed = "0.0.0.0/0" }
	configText := fmt.Sprintf("[Interface]\nPrivateKey = %s\nAddress = %s\nDNS = %s\n\n[Peer]\nPublicKey = %s\nEndpoint = %s\nAllowedIPs = %s\nPersistentKeepalive = 25\n",
		privateKey, input.ClientIP, dns, input.ServerPublicKey, input.Endpoint, allowed)
	d.state[input.PeerID] = peerState{PublicKey: publicKey, Config: configText, Interface: iface, ClientIP: input.ClientIP}
	if err := d.saveState(); err != nil { return nil, err }
	return map[string]any{"public_key": publicKey, "config": configText}, nil
}

func (d *daemon) restorePeers() error {
	for peerID, peer := range d.state {
		if peer.PublicKey == "" || peer.ClientIP == "" || peer.Interface == "" {
			log.Printf("skipping incomplete saved peer %s", peerID)
			continue
		}
		if err := wgRun("set", peer.Interface, "peer", peer.PublicKey, "allowed-ips", hostRoute(peer.ClientIP)); err != nil {
			return fmt.Errorf("restore peer %s: %w", peerID, err)
		}
	}
	return nil
}

func (d *daemon) deletePeer(peerID string) (map[string]any, error) {
	if saved, ok := d.state[peerID]; ok {
		if err := wgRun("set", saved.Interface, "peer", saved.PublicKey, "remove"); err != nil { return nil, err }
		delete(d.state, peerID)
		if err := d.saveState(); err != nil { return nil, err }
	}
	return map[string]any{"status": "removed"}, nil
}

func (d *daemon) traffic(iface string) (map[string]any, error) {
	output, err := wg("show", iface, "dump"); if err != nil { return nil, err }
	return parseTrafficDump(output), nil
}

func parseTrafficDump(output string) map[string]any {
	peers := make([]map[string]any, 0)
	lines := strings.Split(output, "\n")
	for _, line := range lines[1:] {
		parts := strings.Split(line, "\t"); if len(parts) < 8 { continue }
		rx, e1 := strconv.ParseInt(parts[5], 10, 64); tx, e2 := strconv.ParseInt(parts[6], 10, 64)
		if e1 == nil && e2 == nil { peers = append(peers, map[string]any{"public_key": parts[0], "rx_bytes": rx, "tx_bytes": tx}) }
	}
	return map[string]any{"peers": peers}
}

func (d *daemon) loadState() error {
	data, err := os.ReadFile(d.cfg.StatePath)
	if errors.Is(err, os.ErrNotExist) { return nil }; if err != nil { return err }
	plain, err := d.decrypt(data); if err != nil { return fmt.Errorf("decrypt node state: %w", err) }
	return json.Unmarshal(plain, &d.state)
}

func (d *daemon) saveState() error {
	plain, err := json.Marshal(d.state); if err != nil { return err }
	data, err := d.encrypt(plain); if err != nil { return err }
	if err := os.MkdirAll(filepath.Dir(d.cfg.StatePath), 0700); err != nil { return err }
	tmp := d.cfg.StatePath + ".tmp"
	if err := os.WriteFile(tmp, data, 0600); err != nil { return err }
	return os.Rename(tmp, d.cfg.StatePath)
}

func (d *daemon) cipher() (cipher.AEAD, error) {
	key := sha256.Sum256([]byte(d.cfg.APIKey + "\x00wg-node-local-state"))
	block, err := aes.NewCipher(key[:]); if err != nil { return nil, err }
	return cipher.NewGCM(block)
}

func (d *daemon) encrypt(data []byte) ([]byte, error) {
	aead, err := d.cipher(); if err != nil { return nil, err }
	nonce := make([]byte, aead.NonceSize()); if _, err := rand.Read(nonce); err != nil { return nil, err }
	return append(nonce, aead.Seal(nil, nonce, data, nil)...), nil
}

func (d *daemon) decrypt(data []byte) ([]byte, error) {
	aead, err := d.cipher(); if err != nil { return nil, err }
	if len(data) < aead.NonceSize() { return nil, errors.New("invalid encrypted state") }
	return aead.Open(nil, data[:aead.NonceSize()], data[aead.NonceSize():], nil)
}

func wg(args ...string) (string, error) { return wgRunOutput("wg", args...) }

func wgRun(args ...string) error {
	cmd := exec.Command("wg", args...)
	if out, err := cmd.CombinedOutput(); err != nil { return fmt.Errorf("wg %s: %s: %w", strings.Join(args, " "), strings.TrimSpace(string(out)), err) }
	return nil
}

func wgRunOutput(program string, args ...string) (string, error) {
	cmd := exec.Command(program, args...)
	out, err := cmd.Output()
	if err != nil {
		var exit *exec.ExitError
		if errors.As(err, &exit) { return "", fmt.Errorf("%s %s: %s", program, strings.Join(args, " "), strings.TrimSpace(string(exit.Stderr))) }
		return "", err
	}
	return strings.TrimSpace(string(out)), nil
}

func wgInput(input string, args ...string) (string, error) {
	cmd := exec.Command("wg", args...); cmd.Stdin = strings.NewReader(input)
	out, err := cmd.Output(); if err != nil { return "", err }; return strings.TrimSpace(string(out)), nil
}

func hostRoute(address string) string {
	if strings.Contains(address, "/") { return address }
	if strings.Contains(address, ":") { return address + "/128" }
	return address + "/32"
}

func stringValue(v any, fallback string) string {
	if s, ok := v.(string); ok && s != "" { return s }; return fallback
}

func sleep(ctx context.Context, duration time.Duration) bool {
	timer := time.NewTimer(duration); defer timer.Stop()
	select { case <-ctx.Done(): return false; case <-timer.C: return true }
}

func main() {
	cfg, err := loadConfig(); if err != nil { log.Fatal(err) }
	d, err := newDaemon(cfg); if err != nil { log.Fatal(err) }
	ctx, cancel := context.WithCancel(context.Background()); defer cancel()
	if err := d.run(ctx); err != nil { log.Fatal(err) }
}
