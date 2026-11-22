// Package askpass reads one session credential from the Python-owned loopback broker.
package askpass

import (
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
)

func Validate(endpoint, token string) error {
	u, err := url.Parse(endpoint)
	if err != nil {
		return fmt.Errorf("invalid password broker")
	}
	port, err := strconv.Atoi(u.Port())
	if err != nil || port < 1 || port > 65535 || u.Scheme != "http" || u.Hostname() != "127.0.0.1" ||
		u.User != nil || u.Path != "/password" || u.RawQuery != "" || u.Fragment != "" ||
		len(token) < 32 || len(token) > 128 || strings.ContainsAny(token, " \r\n\t") {
		return fmt.Errorf("invalid password broker")
	}
	return nil
}

func Read(endpoint, token string) ([]byte, error) {
	if err := Validate(endpoint, token); err != nil {
		return nil, err
	}
	client := &http.Client{Timeout: 3 * time.Second, Transport: &http.Transport{Proxy: nil},
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	defer client.CloseIdleConnections()
	req, err := http.NewRequest("GET", endpoint, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+token)
	response, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("password broker unavailable")
	}
	defer response.Body.Close()
	if response.StatusCode != 200 {
		return nil, fmt.Errorf("password expired or unavailable")
	}
	value, err := io.ReadAll(io.LimitReader(response.Body, 4097))
	if err != nil || len(value) == 0 || len(value) > 4096 || strings.ContainsAny(string(value), "\r\n\x00") {
		return nil, fmt.Errorf("invalid password response")
	}
	return value, nil
}

// Run must run before monitoring bootstrap and must never log credential values.
func Run() int {
	value, err := Read(os.Getenv("ODESSY_SSH_PASSWORD_URL"), os.Getenv("ODESSY_SSH_PASSWORD_TOKEN"))
	if err != nil {
		return 1
	}
	defer clear(value)
	if _, err = os.Stdout.Write(append(value, '\n')); err != nil {
		return 1
	}
	return 0
}
