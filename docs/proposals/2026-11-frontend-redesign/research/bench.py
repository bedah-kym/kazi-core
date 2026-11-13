import os, time, hmac, hashlib, sqlite3, random, re, tempfile
DB = os.path.join(tempfile.mkdtemp(), 'bench.db')
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
random.seed(1)
vocab = [''.join(random.choice('abcdefghijklmnopqrstuvwxyz') for _ in range(random.randint(3, 9))) for _ in range(20000)]
weights = [1 / (i + 1) for i in range(len(vocab))]  # Zipf-like
N = 50000
msgs = [' '.join(random.choices(vocab, weights, k=60)) for _ in range(N)]  # ~400 bytes each
key = AESGCM.generate_key(256); aes = AESGCM(key)
enc = []
for m in msgs:
    n = os.urandom(12); enc.append((n, aes.encrypt(n, m.encode(), None)))
needle = vocab[1500]
t = time.perf_counter(); hits = [i for i, (n, c) in enumerate(enc) if needle in aes.decrypt(n, c, None).decode()]; scan = time.perf_counter() - t
print(f"messages={N} avg_bytes={sum(map(len, msgs))//N}")
print(f"A decrypt-and-scan every message: {scan*1000:.0f} ms, hits={len(hits)}")
skey = hashlib.sha256(key + b'search').digest()
tok = lambda w: hmac.new(skey, w.encode(), hashlib.sha256).digest()[:8]
db = sqlite3.connect(DB); db.execute("drop table if exists idx")
db.execute("create table idx(room int, tok blob, msg int, primary key(room, tok, msg)) without rowid")
t = time.perf_counter(); rows = []
for i, m in enumerate(msgs):
    for w in set(re.findall(r'[a-z0-9]{3,}', m)): rows.append((1, tok(w), i))
build = time.perf_counter() - t
db.executemany("insert into idx values(?,?,?)", rows); db.commit()
print(f"B index build: {build*1e6/N:.0f} us per message of hashing, {len(rows)} postings, db={os.path.getsize(DB)/1e6:.1f} MB (ciphertext total {sum(len(c) for _, c in enc)/1e6:.1f} MB)")
t = time.perf_counter()
ids = [r[0] for r in db.execute("select msg from idx where room=1 and tok=? order by msg desc limit 20", (tok(needle),))]
snips = [aes.decrypt(*enc[i], None) for i in ids]; q = time.perf_counter() - t
print(f"B query (1 term, newest 20, decrypt those 20): {q*1000:.2f} ms")
w2 = vocab[40]
t = time.perf_counter()
ids = [r[0] for r in db.execute("select a.msg from idx a join idx b on b.room=a.room and b.msg=a.msg where a.room=1 and a.tok=? and b.tok=? order by a.msg desc limit 20", (tok(needle), tok(w2)))]
snips = [aes.decrypt(*enc[i], None) for i in ids]; q2 = time.perf_counter() - t
print(f"B query (2 terms AND, newest 20): {q2*1000:.2f} ms, hits={len(ids)}")
