// Command fakessh is a deterministic test double for the fixed SSH commands.
// It never authenticates or contacts a network endpoint.
package main

import (
	"fmt"
	"os"
	"os/exec"
	"strings"
)

const status = "0.08 0.05 0.07 1/445 4705\n" +
	"---\n" +
	"Filesystem 1024-blocks Used Available Capacity Mounted on\n" +
	"/dev/fake 243521944 74926160 156152700 33% /\n" +
	"---\n" +
	"Linux\n" +
	"aarch64\n"

func main() {
	// Opt-in Python integration fixture: use real OpenSSH for local config
	// resolution, but never open a network connection for the fixed commands.
	if len(os.Args) > 1 && (os.Args[1] == "-G" || os.Args[1] == "-V") {
		binary := os.Getenv("ODESSY_TEST_REAL_SSH")
		if binary == "" {
			os.Exit(2)
		}
		cmd := exec.Command(binary, os.Args[1:]...)
		cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
		if cmd.Run() != nil {
			os.Exit(2)
		}
		return
	}
	if password := os.Getenv("ODESSY_TEST_SSH_PASSWORD"); password != "" {
		helper := os.Getenv("SSH_ASKPASS")
		if helper == "" {
			os.Exit(255)
		}
		cmd := exec.Command(helper, "fixture computer password:")
		output, err := cmd.Output()
		if err != nil || strings.TrimSpace(string(output)) != password {
			fmt.Fprintln(os.Stderr, "Permission denied (password).")
			os.Exit(255)
		}
	}
	remote := ""
	if len(os.Args) > 1 {
		remote = os.Args[len(os.Args)-1]
	}
	switch {
	case strings.Contains(remote, "odessy-connect-ok"):
		fmt.Print("odessy-connect-ok\nubuntu-fixture\n")
	case strings.Contains(remote, "printf odessy-ssh-ok"):
		fmt.Print("odessy-ssh-ok")
	case strings.Contains(remote, "cat /proc/loadavg"):
		fmt.Print(status)
	default:
		os.Exit(2)
	}
}
