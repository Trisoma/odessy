// Command fakessh is a deterministic test double for the fixed SSH commands.
// It never authenticates or contacts a network endpoint.
package main

import (
	"fmt"
	"os"
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
	remote := ""
	if len(os.Args) > 1 {
		remote = os.Args[len(os.Args)-1]
	}
	switch {
	case strings.Contains(remote, "printf odessy-ssh-ok"):
		fmt.Print("odessy-ssh-ok")
	case strings.Contains(remote, "cat /proc/loadavg"):
		fmt.Print(status)
	default:
		os.Exit(2)
	}
}
