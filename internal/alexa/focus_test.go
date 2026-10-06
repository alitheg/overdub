package alexa

import (
	"slices"
	"strings"
	"sync"
	"testing"
	"time"
)

type levels struct {
	mu   sync.Mutex
	seen []int
}

func focusWatcher() (*FocusWatcher, *levels) {
	l := &levels{}
	w := &FocusWatcher{
		OnLevel: func(percent int) {
			l.mu.Lock()
			defer l.mu.Unlock()
			l.seen = append(l.seen, percent)
		},
	}
	return w, l
}

func (l *levels) heard() []int {
	l.mu.Lock()
	defer l.mu.Unlock()
	return append([]int(nil), l.seen...)
}

func (l *levels) last() int {
	seen := l.heard()
	if len(seen) == 0 {
		return fullPercent
	}
	return seen[len(seen)-1]
}

const (
	requestFirst  = "I/MediaFocusControl(  601):  AudioFocus  requestAudioFocus() from com.amazon.media.AmazonAudioManager@37705de9amazon.speech.util.AudioFocusHelper$2@22ab2a6e req=3flags=0x0"
	requestSecond = "I/MediaFocusControl(  601):  AudioFocus  requestAudioFocus() from com.amazon.media.AmazonAudioManager@2b124a00amazon.speech.util.AudioFocusHelper$2@2555b239 req=3flags=0x0"
	abandonFirst  = "I/MediaFocusControl(  601):  AudioFocus  abandonAudioFocus() from com.amazon.media.AmazonAudioManager@37705de9amazon.speech.util.AudioFocusHelper$2@22ab2a6e"
	removeFirst   = "I/MediaFocusControl(  601): AudioFocus  removeFocusStackEntry(): removing entry for com.amazon.media.AmazonAudioManager@37705de9amazon.speech.util.AudioFocusHelper$2@22ab2a6e"
	abandonSecond = "I/MediaFocusControl(  601):  AudioFocus  abandonAudioFocus() from com.amazon.media.AmazonAudioManager@2b124a00amazon.speech.util.AudioFocusHelper$2@2555b239"

	otherClient = "android.media.AudioManager@1a2b3c4dcom.example.Player$1@5e6f7a8b"
)

func requestFrom(client string, req string) string {
	return "I/MediaFocusControl(  601):  AudioFocus  requestAudioFocus() from " + client +
		" req=" + req + "flags=0x0"
}

func abandonFrom(client string) string {
	return "I/MediaFocusControl(  601):  AudioFocus  abandonAudioFocus() from " + client
}

func lines(ls ...string) *strings.Reader {
	return strings.NewReader(strings.Join(ls, "\n") + "\n")
}

func TestOverlappingRequestsHoldTheDuckUntilTheLastAbandon(t *testing.T) {
	w, l := focusWatcher()

	w.read(lines(requestFirst, requestSecond, abandonFirst))
	if got := l.last(); got != duckPercent {
		t.Fatalf("level %d after one of two holders abandoned, want %d: her requests overlap "+
			"and the second is still speaking", got, duckPercent)
	}

	w.read(lines(removeFirst, abandonSecond))
	if got := l.heard(); !slices.Equal(got, []int{duckPercent, fullPercent}) {
		t.Errorf("heard levels %v, want [%d %d]", got, duckPercent, fullPercent)
	}
}

func TestARemovedStackEntryReleasesItsHold(t *testing.T) {
	w, l := focusWatcher()

	w.read(lines(requestFirst, requestSecond, removeFirst, abandonSecond))
	if got := l.heard(); !slices.Equal(got, []int{duckPercent, fullPercent}) {
		t.Errorf("heard levels %v, want [%d %d]", got, duckPercent, fullPercent)
	}
}

func TestARemovalStraightAfterTheSameClientsRequestKeepsItsHold(t *testing.T) {
	w, l := focusWatcher()

	w.read(lines(requestFirst, requestSecond, requestFirst, removeFirst, abandonSecond))
	if got := l.last(); got != duckPercent {
		t.Errorf("level %d after a re-request moved the first client up the stack, want %d",
			got, duckPercent)
	}
	w.read(lines(abandonFirst))
	if got := l.last(); got != fullPercent {
		t.Errorf("level %d after the last abandon, want %d", got, fullPercent)
	}
}

func TestAPermanentGainDoesNotDuck(t *testing.T) {
	w, l := focusWatcher()

	w.read(lines(requestFrom(otherClient, "1"), abandonFrom(otherClient)))
	if got := l.heard(); len(got) != 0 {
		t.Errorf("heard levels %v for a permanent taker, want none: it plays alongside", got)
	}
}

func TestATransientGainDucksDeeperThanAMayDuck(t *testing.T) {
	w, l := focusWatcher()

	w.read(lines(requestFirst, requestFrom(otherClient, "2")))
	if got := l.last(); got != deepDuckPercent {
		t.Fatalf("level %d with a req=2 and a req=3 holder, want %d: the deepest wins",
			got, deepDuckPercent)
	}

	w.read(lines(abandonFrom(otherClient)))
	if got := l.heard(); !slices.Equal(got, []int{duckPercent, deepDuckPercent, duckPercent}) {
		t.Errorf("heard levels %v, want [%d %d %d]", got, duckPercent, deepDuckPercent, duckPercent)
	}
}

func TestAnExclusiveGainDucksDeep(t *testing.T) {
	w, l := focusWatcher()

	w.read(lines(requestFrom(otherClient, "4")))
	if got := l.last(); got != deepDuckPercent {
		t.Errorf("level %d for req=4, want %d", got, deepDuckPercent)
	}
}

func TestTheLevelIsOnlySentWhenItChanges(t *testing.T) {
	w, l := focusWatcher()

	w.read(lines(requestFirst, requestSecond, requestFirst, abandonFirst, abandonFirst,
		abandonSecond, abandonSecond, removeFirst))
	if got := l.heard(); !slices.Equal(got, []int{duckPercent, fullPercent}) {
		t.Errorf("heard levels %v, want [%d %d]: a level already set is not news",
			got, duckPercent, fullPercent)
	}
}

func TestAReRequestReplacesTheClientsOldRequest(t *testing.T) {
	w, l := focusWatcher()

	w.read(lines(requestFrom(otherClient, "3"), requestFrom(otherClient, "2")))
	if got := l.last(); got != deepDuckPercent {
		t.Fatalf("level %d after req=3 then req=2 from one client, want %d", got, deepDuckPercent)
	}

	w.read(lines(requestFrom(otherClient, "1")))
	if got := l.heard(); !slices.Equal(got, []int{duckPercent, deepDuckPercent, fullPercent}) {
		t.Errorf("heard levels %v, want [%d %d %d]: a client holds one request, its latest",
			got, duckPercent, deepDuckPercent, fullPercent)
	}
}

func TestAHoldNobodyReleasesExpires(t *testing.T) {
	w, l := focusWatcher()
	now := time.Now()
	w.now = func() time.Time { return now }

	w.read(lines(requestFirst))
	now = now.Add(holdFor - time.Minute)
	w.read(lines(requestSecond))

	now = now.Add(2 * time.Minute)
	w.expire()
	if got := l.heard(); !slices.Equal(got, []int{duckPercent}) {
		t.Fatalf("heard levels %v after the first hold expired, want [%d]: the second "+
			"was requested later and has not", got, duckPercent)
	}

	now = now.Add(holdFor)
	w.expire()
	if got := l.heard(); !slices.Equal(got, []int{duckPercent, fullPercent}) {
		t.Fatalf("heard levels %v, want [%d %d]: a missed abandon would otherwise leave "+
			"the music ducked for good", got, duckPercent, fullPercent)
	}

	w.expire()
	if got := l.heard(); len(got) != 2 {
		t.Errorf("heard levels %v; a sweep with nothing to expire sent a level", got)
	}
}

func TestALogcatThatEndsLeavesTheMusicAtFullLevel(t *testing.T) {
	w, l := focusWatcher()

	w.read(lines(requestFirst, requestFrom(otherClient, "2")))
	w.reset()
	w.reset()
	if got := l.heard(); !slices.Equal(got, []int{duckPercent, deepDuckPercent, fullPercent}) {
		t.Fatalf("heard levels %v, want [%d %d %d]: the abandons logcat missed while it "+
			"was down will never arrive", got, duckPercent, deepDuckPercent, fullPercent)
	}

	w.read(lines(abandonFirst))
	if got := l.heard(); len(got) != 3 {
		t.Errorf("heard levels %v; a holder survived the reset", got)
	}
}

func TestAResetAtFullLevelSendsNothing(t *testing.T) {
	w, l := focusWatcher()

	w.reset()
	if got := l.heard(); len(got) != 0 {
		t.Errorf("heard levels %v from a reset with nothing held, want none", got)
	}
}

func TestLinesThatAreNotFocusChangesAreIgnored(t *testing.T) {
	w, l := focusWatcher()

	w.read(lines(
		"",
		"--------- beginning of main",
		"I/MediaFocusControl(  601):  AudioFocus  requestAudioFocus() from "+otherClient,
		"I/MediaFocusControl(  601):  AudioFocus  requestAudioFocus() from "+otherClient+" req=flags=0x0",
		"I/MediaFocusControl(  601):  AudioFocus  requestAudioFocus() from  req=3flags=0x0",
		"I/MediaFocusControl(  601):  AudioFocus  abandonAudioFocus() from ",
		"I/MediaFocusControl(  601):  AudioFocus  removeFocusStackEntry(): removing entry for ",
		"D/MediaFocusControl(  601):  dispatchFocusChange(-3) to "+otherClient,
		"I/tts-Server(  940): Playback started: uid(32037)_id(0)_namespace(SpeechSynthesizer)",
	))
	if got := l.heard(); len(got) != 0 {
		t.Errorf("heard levels %v from lines that request nothing, want none", got)
	}
}
