package audio

import (
	"math"
	"slices"
	"testing"
	"time"
)

const duckProbe = 30000

func ducked(cut float64) *Stream {
	s := quiet()
	s.cut, s.cutTo = cut, cut
	return s
}

func gains(s *Stream, frames int) []int16 {
	var out []int16
	block := make([]int16, BlockSamples)
	for len(out) < frames {
		for i := range block {
			block[i] = duckProbe
		}
		s.scale(block)
		for i := 0; i < len(block); i += ChimeChannels {
			if block[i] != block[i+1] {
				panic("the channels of one frame were scaled apart")
			}
		}
		out = append(out, left(block)...)
	}
	return out[:frames]
}

func firstAt(out []int16, want int16) int {
	return slices.Index(out, want)
}

func TestAFullGainStreamIsBitIdenticalToAnUnduckedOne(t *testing.T) {
	now := time.Now()
	plain, full := quiet(), quiet()
	full.duck(duckCut(100))
	for _, s := range []*Stream{plain, full} {
		anchorAt(s, now)
		if err := s.Write(now, stereoRamp(0, 4*BlockFrames)); err != nil {
			t.Fatalf("Write: %v", err)
		}
	}
	a, b := make([]int16, BlockSamples), make([]int16, BlockSamples)
	for n := range 4 {
		plain.read(a)
		full.read(b)
		if !slices.Equal(a, b) {
			t.Fatalf("block %d differs at full gain; the normal path has to leave every"+
				" sample as the server sent it", n)
		}
	}
	if full.cut != 0 || full.cutTo != 0 {
		t.Errorf("a stream at full level holds cut %v toward %v", full.cut, full.cutTo)
	}
}

func TestADuckRampsDownOverFiftyMillisecondsAFullSwing(t *testing.T) {
	s := quiet()
	s.duck(duckCut(20))
	swing := frameCount(ChimeRate, duckDownFor)
	out := gains(s, int(swing)+BlockFrames)
	floor := int16(math.Round(duckProbe * 0.2))
	at := firstAt(out, floor)
	if at < 0 || at >= int(swing) {
		t.Fatalf("the gain reached 20%% at frame %d, want inside the %d frames of %s",
			at, swing, duckDownFor)
	}
	if want := int(swing) * 8 / 10; at < want-2 {
		t.Errorf("the gain reached 20%% at frame %d, well before the %d frames a linear"+
			" ramp takes; the duck stepped rather than ramped", at, want)
	}
	step := int16(duckProbe/swing) + 1
	prev := int16(duckProbe)
	for i, got := range out {
		if got > prev || prev-got > step {
			t.Fatalf("frame %d went from %d to %d; a duck only falls, and by at most %d"+
				" a frame", i, prev, got, step)
		}
		if got < floor {
			t.Fatalf("frame %d is %d, under the 20%% target of %d", i, got, floor)
		}
		prev = got
	}
}

func TestReturningToFullRampsUpOverThreeHundredMilliseconds(t *testing.T) {
	s := ducked(duckCut(20))
	s.duck(duckCut(100))
	swing := frameCount(ChimeRate, duckUpFor)
	out := gains(s, int(swing)+BlockFrames)
	at := firstAt(out, duckProbe)
	if at < 0 || at >= int(swing) {
		t.Fatalf("the gain came back to full at frame %d, want inside the %d frames of %s",
			at, swing, duckUpFor)
	}
	if want := int(swing) * 8 / 10; at < want-2 {
		t.Errorf("the gain came back at frame %d, well before the %d frames a linear ramp"+
			" takes", at, want)
	}
	step := int16(duckProbe/swing) + 1
	prev := int16(math.Round(duckProbe * 0.2))
	for i, got := range out {
		if got < prev || got-prev > step {
			t.Fatalf("frame %d went from %d to %d; a return only rises, and by at most %d"+
				" a frame", i, prev, got, step)
		}
		if got > duckProbe {
			t.Fatalf("frame %d is %d, louder than the stream sent", i, got)
		}
		prev = got
	}
	if s.cut != 0 || s.cutTo != 0 {
		t.Errorf("a stream back at full holds cut %v toward %v, so it never returns to"+
			" the untouched path", s.cut, s.cutTo)
	}
}

func TestADuckChangedMidRampTurnsWithoutAJump(t *testing.T) {
	s := quiet()
	s.duck(duckCut(20))
	out := gains(s, 2*BlockFrames)
	s.duck(duckCut(100))
	out = append(out, gains(s, 4*BlockFrames)...)
	s.duck(duckCut(10))
	out = append(out, gains(s, 6*BlockFrames)...)
	step := int16(duckProbe/frameCount(ChimeRate, duckDownFor)) + 1
	for i := 1; i < len(out); i++ {
		if d := out[i] - out[i-1]; d > step || d < -step {
			t.Fatalf("frame %d stepped by %d; no change of target may click", i, d)
		}
	}
	if got, want := out[len(out)-1], int16(math.Round(duckProbe*0.1)); got != want {
		t.Errorf("the last frame is %d, want %d at 10%%", got, want)
	}
}

func TestAStreamOpenedDuckedStartsAtItsLevel(t *testing.T) {
	s := ducked(duckCut(20))
	now := time.Now()
	anchorAt(s, now)
	if err := s.Write(now, level(BlockFrames, duckProbe)); err != nil {
		t.Fatalf("Write: %v", err)
	}
	block := make([]int16, BlockSamples)
	s.read(block)
	want := attenuate(duckProbe, 1-duckCut(20))
	for i, got := range block {
		if got != want {
			t.Fatalf("sample %d is %d, want %d; a stream opened while Alexa holds focus"+
				" starts at her level rather than ramping down to it", i, got, want)
		}
	}
	if d := want - int16(math.Round(duckProbe*0.2)); d > 1 || d < -1 {
		t.Errorf("20%% of %d came out %d", duckProbe, want)
	}
}

func TestDuckPercentsAreClamped(t *testing.T) {
	for _, c := range []struct {
		percent int
		cut     float64
	}{{-50, 1}, {0, 1}, {20, 0.8}, {100, 0}, {101, 0}, {1000, 0}} {
		if got := duckCut(c.percent); got != c.cut {
			t.Errorf("duckCut(%d) = %v, want %v", c.percent, got, c.cut)
		}
	}
	s := quiet()
	s.duck(2)
	if s.cutTo != 1 {
		t.Errorf("a cut of 2 was held as %v, deeper than silence", s.cutTo)
	}
	s.duck(-1)
	if s.cutTo != 0 {
		t.Errorf("a cut of -1 was held as %v, louder than the stream", s.cutTo)
	}
}

func TestAttenuatingRoundsAndStaysInRange(t *testing.T) {
	for _, c := range []struct {
		in   int16
		gain float64
		want int16
	}{
		{math.MinInt16, 1, math.MinInt16},
		{math.MaxInt16, 1, math.MaxInt16},
		{math.MaxInt16, 0.5, 16384},
		{math.MinInt16, 0.5, -16384},
		{-3, 0.5, -2},
		{1000, 0, 0},
	} {
		if got := attenuate(c.in, c.gain); got != c.want {
			t.Errorf("attenuate(%d, %v) = %d, want %d", c.in, c.gain, got, c.want)
		}
	}
}

func TestTheChimeIsMixedAtFullLevelOverADuckedStream(t *testing.T) {
	m := newMixer()
	s := ducked(duckCut(10))
	c := &clip{pcm: decode(level(BlockFrames, 4000))}
	m.add(s)
	m.add(c)
	block := make([]int16, BlockSamples)
	m.next(block)
	for i, got := range block {
		if got != 4000 {
			t.Fatalf("sample %d is %d; the chime was ducked with the stream", i, got)
		}
	}
}

func TestADuckLandsWhileTheStreamIsRead(t *testing.T) {
	s := quiet()
	now := time.Now()
	anchorAt(s, now)
	done := make(chan struct{})
	go func() {
		defer close(done)
		block := make([]int16, BlockSamples)
		for range 200 {
			s.read(block)
		}
	}()
	for i := range 200 {
		s.duck(duckCut(i % 101))
	}
	<-done
	s.Close()
}
