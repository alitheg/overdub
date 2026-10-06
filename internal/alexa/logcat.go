package alexa

import (
	"bufio"
	"io"
	"log"
	"os/exec"
	"syscall"
	"time"
)

func keepFollowing(name string, argv []string, line func(string), ended func()) {
	said := false
	for {
		start := time.Now()
		err := follow(argv, line)
		if ended != nil {
			ended()
		}
		var say bool
		say, said = shouldSay(said, time.Since(start), err)
		if say {
			log.Printf("%s: %v; retrying every %v, and saying so once", name, err, watchRetry)
		}
		time.Sleep(watchRetry)
	}
}

func follow(argv []string, line func(string)) error {
	cmd := exec.Command(argv[0], argv[1:]...)
	cmd.SysProcAttr = &syscall.SysProcAttr{Pdeathsig: syscall.SIGKILL}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	if err := cmd.Start(); err != nil {
		return err
	}
	defer func() {
		_ = cmd.Process.Kill()
		_ = cmd.Wait()
	}()

	return readLines(stdout, line)
}

func readLines(r io.Reader, line func(string)) error {
	sc := bufio.NewScanner(r)
	for sc.Scan() {
		line(sc.Text())
	}
	return sc.Err()
}
