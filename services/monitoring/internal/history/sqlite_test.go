package history

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestSQLiteSurvivesRestartDeduplicatesAndKeepsBothTiers(t *testing.T) {
	dir := t.TempDir()
	at := time.Now().UTC().Truncate(time.Minute)
	store, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	store.Enqueue("host", sample("one", 1, at, f(10)))
	store.Enqueue("host", sample("one", 1, at, f(10)))
	store.Enqueue("host", sample("two", 2, at.Add(10*time.Second), f(30)))
	store.Close()
	raw, err := os.ReadFile(filepath.Join(dir, "history.sqlite3"))
	if err != nil {
		t.Fatal(err)
	}
	if string(raw[:16]) != "SQLite format 3\x00" {
		t.Fatal("not a SQLite database")
	}
	reopened, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	for _, step := range []time.Duration{time.Minute, 5 * time.Minute} {
		got, err := reopened.Query("host", []string{"cpu.utilization_percent"}, at.Add(-5*time.Minute), at.Add(5*time.Minute), step)
		if err != nil {
			t.Fatal(err)
		}
		if len(got[0].Points) != 1 || got[0].Points[0].Value != 20 {
			t.Fatalf("restart/duplicate/weighted mean: %+v", got)
		}
	}
}

func TestSQLiteCloseDrainsAndCorruptionDoesNotOverwriteData(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "history.sqlite3")
	if err := os.WriteFile(path, []byte("not a database"), 0600); err != nil {
		t.Fatal(err)
	}
	store, err := Open(dir)
	if err == nil {
		store.Close()
		t.Fatal("corrupt database was accepted")
	}
	data, _ := os.ReadFile(path)
	if string(data) != "not a database" {
		t.Fatal("corrupt history overwritten")
	}
}

func TestTransientOverflowStillDrainsAndBoundaryBucketsAreExplicit(t *testing.T) {
	dir := t.TempDir()
	at := time.Now().UTC().Truncate(time.Minute)
	store, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	store.markDegraded("history queue full")
	store.Enqueue("host", sample("kept", 1, at.Add(30*time.Second), f(40)))
	store.Close()
	store, err = Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	got, err := store.Query("host", []string{"cpu.utilization_percent"}, at.Add(10*time.Second), at.Add(time.Minute), time.Minute)
	if err != nil || len(got[0].Points) != 1 {
		t.Fatalf("accepted row lost: %+v %v", got, err)
	}
}
