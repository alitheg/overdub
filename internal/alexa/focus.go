package alexa

import (
	"io"
	"log"
	"strings"
	"sync"
	"time"
)

const (
	duckPercent     = 20
	deepDuckPercent = 10
	fullPercent     = 100
	holdFor         = 15 * time.Minute

	requestMark = "requestAudioFocus() from "
	reqMark     = " req="
	abandonMark = "abandonAudioFocus() from "
	removeMark  = "removeFocusStackEntry(): removing entry for "
)

var focusArgv = []string{"/system/bin/logcat", "-T", "1", "-v", "brief", "-s", "MediaFocusControl"}

type focusHolder struct {
	req   int
	until time.Time
}

type FocusWatcher struct {
	OnLevel func(percent int)

	notify    sync.Mutex
	mu        sync.Mutex
	requested string
	holders   map[string]focusHolder
	level     int
	now       func() time.Time
}

func (w *FocusWatcher) clock() time.Time {
	if w.now != nil {
		return w.now()
	}
	return time.Now()
}

func (w *FocusWatcher) Run() {
	go w.sweep()
	keepFollowing("focus watcher", focusArgv, w.line, w.reset)
}

func (w *FocusWatcher) sweep() {
	for range time.Tick(sweepEvery) {
		w.expire()
	}
}

func (w *FocusWatcher) read(r io.Reader) error {
	return readLines(r, w.line)
}

func (w *FocusWatcher) line(line string) {
	if i := strings.Index(line, requestMark); i >= 0 {
		rest := line[i+len(requestMark):]
		j := strings.LastIndex(rest, reqMark)
		if j < 0 {
			return
		}
		req, ok := leadingInt(rest[j+len(reqMark):])
		if !ok {
			return
		}
		client := strings.TrimSpace(rest[:j])
		w.request(client, req)
		w.requested = client
		return
	}
	if i := strings.Index(line, abandonMark); i >= 0 {
		w.requested = ""
		w.release(strings.TrimSpace(line[i+len(abandonMark):]))
		return
	}
	if i := strings.Index(line, removeMark); i >= 0 {
		client := strings.TrimSpace(line[i+len(removeMark):])
		requested := w.requested
		w.requested = ""
		if client == requested {
			return
		}
		w.release(client)
	}
}

func leadingInt(s string) (int, bool) {
	n, digits := 0, 0
	for digits < len(s) && digits < 9 && s[digits] >= '0' && s[digits] <= '9' {
		n = n*10 + int(s[digits]-'0')
		digits++
	}
	return n, digits > 0
}

func (w *FocusWatcher) request(client string, req int) {
	if client == "" {
		return
	}
	w.change(func() {
		if w.holders == nil {
			w.holders = map[string]focusHolder{}
		}
		w.holders[client] = focusHolder{req: req, until: w.clock().Add(holdFor)}
	})
}

func (w *FocusWatcher) release(client string) {
	if client == "" {
		return
	}
	w.change(func() {
		delete(w.holders, client)
	})
}

func (w *FocusWatcher) expire() {
	dropped := 0
	w.change(func() {
		now := w.clock()
		for client, h := range w.holders {
			if now.After(h.until) {
				delete(w.holders, client)
				dropped++
			}
		}
	})
	if dropped > 0 {
		log.Printf("focus watcher: %d clients never abandoned audio focus, so their hold"+
			" was dropped after %v", dropped, holdFor)
	}
}

func (w *FocusWatcher) reset() {
	w.requested = ""
	w.change(func() {
		clear(w.holders)
	})
}

func (w *FocusWatcher) change(mutate func()) {
	w.notify.Lock()
	defer w.notify.Unlock()

	w.mu.Lock()
	mutate()
	level := fullPercent
	for _, h := range w.holders {
		level = min(level, percentFor(h.req))
	}
	was := w.level
	if was == 0 {
		was = fullPercent
	}
	w.level = level
	w.mu.Unlock()

	if level != was && w.OnLevel != nil {
		w.OnLevel(level)
	}
}

func percentFor(req int) int {
	switch req {
	case 3:
		return duckPercent
	case 2, 4:
		return deepDuckPercent
	}
	return fullPercent
}
