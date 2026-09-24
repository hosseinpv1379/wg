package main

import (
	"bytes"
	"testing"
)

func TestHostRoute(t *testing.T) {
	tests := map[string]string{
		"10.44.0.8":     "10.44.0.8/32",
		"10.44.0.8/32":  "10.44.0.8/32",
		"2001:db8::1":   "2001:db8::1/128",
	}
	for input, expected := range tests {
		if actual := hostRoute(input); actual != expected {
			t.Errorf("hostRoute(%q) = %q; want %q", input, actual, expected)
		}
	}
}

func TestPeerStateEncryptionRoundTrip(t *testing.T) {
	d := &daemon{cfg: config{APIKey: "wg_node_test_key_with_sufficient_entropy"}}
	plaintext := []byte(`{"peer":"sample","private":"secret"}`)
	ciphertext, err := d.encrypt(plaintext)
	if err != nil {
		t.Fatal(err)
	}
	if bytes.Contains(ciphertext, []byte("secret")) {
		t.Fatal("encrypted node state contains plaintext")
	}
	decrypted, err := d.decrypt(ciphertext)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(decrypted, plaintext) {
		t.Fatalf("decrypted state = %q; want %q", decrypted, plaintext)
	}
}

func TestParseTrafficDumpUsesWireGuardRXAndTXColumns(t *testing.T) {
	dump := "private\tpublic\tport\tfwmark\n" +
		"peer-public\t(none)\t198.51.100.5:51820\t10.44.0.2/32\t1712345678\t1234\t5678\t25\n"
	result := parseTrafficDump(dump)
	peers := result["peers"].([]map[string]any)
	if len(peers) != 1 || peers[0]["rx_bytes"] != int64(1234) || peers[0]["tx_bytes"] != int64(5678) {
		t.Fatalf("unexpected traffic counters: %#v", result)
	}
}
