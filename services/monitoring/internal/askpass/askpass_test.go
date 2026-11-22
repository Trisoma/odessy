package askpass

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestBrokerBoundary(t *testing.T) {
	token := strings.Repeat("a", 43)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+token {
			w.WriteHeader(403)
			return
		}
		w.Write([]byte("fixture-password"))
	}))
	defer server.Close()
	value, err := Read(server.URL+"/password", token)
	if err != nil || string(value) != "fixture-password" {
		t.Fatal("read failed", err)
	}
	if _, err = Read(server.URL+"/password", strings.Repeat("b", 43)); err == nil {
		t.Fatal("wrong token accepted")
	}
	for _, u := range []string{"http://example.com:80/password", "http://127.0.0.1:80/password?x=y", "http://u@127.0.0.1:80/password", "https://127.0.0.1:80/password"} {
		if Validate(u, token) == nil {
			t.Fatal("unsafe broker accepted", u)
		}
	}
}
func TestRedirectAndOversize(t *testing.T) {
	for _, redirect := range []bool{true, false} {
		s := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if redirect {
				http.Redirect(w, r, "http://example.com", 302)
			} else {
				w.Write([]byte(strings.Repeat("x", 4097)))
			}
		}))
		if _, err := Read(s.URL+"/password", strings.Repeat("a", 43)); err == nil {
			t.Fatal("invalid response accepted")
		}
		s.Close()
	}
}
