package history

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"sync"
	"time"

	"github.com/odessy/odessy/services/monitoring/internal/protocol"
	_ "modernc.org/sqlite"
)

type row struct {
	HostID string
	Sample protocol.Sample
}

// Store owns its own SQLite database, never the Rust trust registry.
type Store struct {
	mu          sync.Mutex
	db          *sql.DB
	dir         string
	queue       chan row
	done        chan struct{}
	closed      bool
	rows, bytes int
	degraded    string
	now         func() time.Time
}

func Open(dir string) (*Store, error) {
	if dir == "" {
		return nil, errors.New("state directory required")
	}
	if err := os.MkdirAll(dir, 0700); err != nil {
		return nil, err
	}
	path := filepath.Join(dir, "history.sqlite3")
	if info, err := os.Lstat(path); err == nil && info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("linked history database refused")
	}
	// Create without truncating existing data; corruption is reported, never erased.
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	file.Close()
	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	_, err = db.Exec(`PRAGMA busy_timeout=1000; PRAGMA page_size=4096; PRAGMA max_page_count=28672; PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;
 CREATE TABLE IF NOT EXISTS seen(host TEXT,id TEXT,at INTEGER,PRIMARY KEY(host,id));
 CREATE TABLE IF NOT EXISTS aggregate(host TEXT,series TEXT,tier INTEGER,bucket INTEGER,
 total REAL NOT NULL,weight REAL NOT NULL,n INTEGER NOT NULL,lo REAL NOT NULL,hi REAL NOT NULL,
 PRIMARY KEY(host,series,tier,bucket));
 PRAGMA wal_autocheckpoint=256; PRAGMA journal_size_limit=4194304;`)
	if err != nil {
		db.Close()
		return nil, fmt.Errorf("history database unavailable: %w", err)
	}
	s := &Store{db: db, dir: dir, queue: make(chan row, protocol.StoreQueueDepth), done: make(chan struct{}), now: time.Now}
	s.refreshStatus()
	go func() {
		defer close(s.done)
		for row := range s.queue {
			if err := s.persist(row); err != nil {
				s.markDegraded("history write failed")
			}
		}
	}()
	return s, nil
}

func (s *Store) persist(r row) error {
	s.mu.Lock()
	degraded := s.degraded == "history write failed" || s.degraded == "history capacity reached"
	s.mu.Unlock()
	if degraded {
		return nil
	}
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	inserted, err := tx.Exec("INSERT OR IGNORE INTO seen(host,id,at) VALUES(?,?,?)", r.HostID, r.Sample.ID, r.Sample.ObservedAt.Unix())
	if err != nil {
		return err
	}
	count, _ := inserted.RowsAffected()
	if count == 0 {
		return tx.Commit()
	}
	for _, ext := range extractors {
		value, ok := ext.get(r.Sample)
		if !ok || math.IsNaN(value) || math.IsInf(value, 0) {
			continue
		}
		for _, tier := range []int64{60, 300} {
			weight := math.Min(float64(tier), math.Max(1, float64(r.Sample.IntervalMS)/1000))
			bucket := r.Sample.ObservedAt.Unix() / tier * tier
			_, err = tx.Exec(`INSERT INTO aggregate(host,series,tier,bucket,total,weight,n,lo,hi) VALUES(?,?,?,?,?,?,1,?,?)
    ON CONFLICT(host,series,tier,bucket) DO UPDATE SET total=total+excluded.total,weight=weight+excluded.weight,n=n+1,lo=min(lo,excluded.lo),hi=max(hi,excluded.hi)`, r.HostID, ext.name, tier, bucket, value*weight, weight, value, value)
			if err != nil {
				return err
			}
		}
	}
	now := s.now().UTC().Unix()
	if _, err = tx.Exec("DELETE FROM aggregate WHERE (tier=60 AND bucket<?) OR (tier=300 AND bucket<?)", now-86400, now-7*86400); err != nil {
		return err
	}
	if _, err = tx.Exec("DELETE FROM seen WHERE at<?", now-7*86400); err != nil {
		return err
	}
	// Bounded pruning covers both aggregate tiers and deduplication state.
	for _, table := range []string{"aggregate", "seen"} {
		if _, err = tx.Exec("DELETE FROM "+table+" WHERE rowid IN (SELECT rowid FROM "+table+" ORDER BY "+map[string]string{"aggregate": "bucket", "seen": "at"}[table]+" LIMIT max(0,(SELECT count(*) FROM "+table+")-?))", protocol.StoreRowCap/2); err != nil {
			return err
		}
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	s.refreshStatus()
	return nil
}

func (s *Store) refreshStatus() {
	var rows int
	_ = s.db.QueryRow("SELECT (SELECT count(*) FROM seen)+(SELECT count(*) FROM aggregate)").Scan(&rows)
	bytes := int64(0)
	for _, name := range []string{"history.sqlite3", "history.sqlite3-wal", "history.sqlite3-shm"} {
		if info, err := os.Stat(filepath.Join(s.dir, name)); err == nil {
			bytes += info.Size()
		}
	}
	s.mu.Lock()
	s.rows = rows
	s.bytes = int(bytes)
	if bytes > protocol.StoreByteBudget {
		s.degraded = "history capacity reached"
	}
	s.mu.Unlock()
}
func (s *Store) markDegraded(reason string) { s.mu.Lock(); s.degraded = reason; s.mu.Unlock() }
func (s *Store) Enqueue(host string, sample protocol.Sample) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.closed || s.degraded == "history write failed" || s.degraded == "history capacity reached" {
		return
	}
	select {
	case s.queue <- row{host, sample}:
	default:
		s.degraded = "history queue full"
	}
}
func (s *Store) Status() (int, int, string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.rows, s.bytes, s.degraded
}
func (s *Store) Close() {
	s.mu.Lock()
	if !s.closed {
		s.closed = true
		close(s.queue)
	}
	s.mu.Unlock()
	<-s.done
	_, _ = s.db.Exec("PRAGMA wal_checkpoint(TRUNCATE)")
	s.db.Close()
}

// Query reads materialized tiers after restart. There is no outage interpolation.
func (s *Store) Query(host string, names []string, from, to time.Time, step time.Duration) ([]Series, error) {
	if len(names) == 0 || len(names) > protocol.MaxHistorySeries {
		return nil, ErrLimit
	}
	if !to.After(from) || step < time.Minute {
		return nil, ErrInvalidRange
	}
	if to.Sub(from)/step+1 > protocol.MaxHistoryPointsPerSeries {
		return nil, ErrLimit
	}
	tier := 60
	if step >= 5*time.Minute {
		tier = 300
	} else if from.Before(s.now().Add(-24 * time.Hour)) {
		return nil, ErrInvalidRange
	}
	if step.Seconds()/float64(tier) != math.Trunc(step.Seconds()/float64(tier)) {
		return nil, ErrInvalidRange
	}
	out := make([]Series, 0, len(names))
	for _, name := range names {
		if _, ok := findExtractor(name); !ok {
			return nil, ErrInvalidSeries
		}
		rows, err := s.db.Query("SELECT bucket,total,weight,n,lo,hi FROM aggregate WHERE host=? AND series=? AND tier=? AND bucket>=? AND bucket<? ORDER BY bucket LIMIT 10001", host, name, tier, from.Unix()/int64(tier)*int64(tier), to.Unix())
		if err != nil {
			return nil, err
		}
		type point struct {
			total, weight float64
			lo, hi        float64
			n             int
		}
		buckets := map[int64]*point{}
		for rows.Next() {
			var at int64
			var total, weight, lo, hi float64
			var n int
			if err = rows.Scan(&at, &total, &weight, &n, &lo, &hi); err != nil {
				break
			}
			key := time.Unix(at, 0).Truncate(step).Unix()
			p := buckets[key]
			if p == nil {
				p = &point{lo: lo, hi: hi}
				buckets[key] = p
			}
			p.total += total
			p.weight += weight
			p.n += n
			p.lo = math.Min(p.lo, lo)
			p.hi = math.Max(p.hi, hi)
		}
		if scanErr := rows.Err(); err == nil {
			err = scanErr
		}
		rows.Close()
		if err != nil {
			return nil, err
		}
		keys := make([]int64, 0, len(buckets))
		for key := range buckets {
			keys = append(keys, key)
		}
		sort.Slice(keys, func(i, j int) bool { return keys[i] < keys[j] })
		result := Series{Name: name}
		for _, key := range keys {
			p := buckets[key]
			result.Points = append(result.Points, Point{At: time.Unix(key, 0).UTC(), Value: p.total / p.weight, Coverage: math.Min(1, p.weight/step.Seconds()), Min: p.lo, Max: p.hi, Count: p.n})
		}
		out = append(out, result)
	}
	raw, _ := json.Marshal(out)
	if len(raw) > protocol.MaxHistoryResponseBytes {
		return nil, ErrLimit
	}
	return out, nil
}
