"""警告門檻與調參的成本計算（training/decision.py、training/tune_fd.py）。"""
from __future__ import annotations

import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from training import decision as T     # noqa: E402
from training import tune_fd            # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  — {detail}" if detail and not cond else ""))


def mk(p_danger):
    p = np.zeros((len(p_danger), 3), np.float32)
    p[:, 2] = p_danger
    p[:, 0] = 1 - np.asarray(p_danger)
    return p


print("\n[counts] 漏報與誤報")
y = np.array([2, 2, 0, 0, 1])
p = mk([0.9, 0.4, 0.6, 0.1, 0.2])
fn, fp, nd = T.counts(y, p, 0.5)
check("τ=0.5：漏報 1（0.4 的危險）、誤報 1（0.6 的安全）", (fn, fp, nd) == (1, 1, 2), str((fn, fp, nd)))
check("τ=0.3：不漏報但誤報 1", T.counts(y, p, 0.3)[:2] == (0, 1))
check("成本 = (R×漏報 + 誤報) / N × 1000",
      abs(T.cost_per_1000(y, p, 0.5, 10) - 1000 * 11 / 5) < 1e-9)

print("\n[best_tau] 漏報越貴，門檻越低（越敢喊）")
rng = np.random.default_rng(0)
y = np.r_[np.full(50, 2), np.zeros(950, int)]
pd = np.r_[rng.uniform(0.3, 1.0, 50), rng.uniform(0.0, 0.8, 950)]
t3, t30 = T.best_tau(y, mk(pd), 3), T.best_tau(y, mk(pd), 30)
check("R=30 選出的門檻 <= R=3 的門檻", t30 <= t3, f"{t30} vs {t3}")
check("門檻落在搜尋範圍內", T.TAU_GRID.min() <= t30 <= T.TAU_GRID.max())

print("\n[score] 門檻只用自己的驗證資料選")
d = {}
for site in ("a", "b"):
    d[f"y_val_{site}"] = y
    for n in ("a", "b"):
        d[f"p_val_{n}_on_{site}"] = mk(pd if site == n else np.full(1000, 0.99))
s = tune_fd.score(d, 10)
check("τ 不受對方路口資料影響（對方全喊危險也不改變 τ）",
      s["tau"]["a"] == T.best_tau(y, mk(pd), 10), str(s["tau"]))
check("四格成本都有算", set(s["cells"]) == {"a->a", "a->b", "b->a", "b->b"})

print("\n[decide] 線上判定（App、評估、提前預警共用）")
from mec_app import model as MM      # noqa: E402
pp = np.array([[0.5, 0.2, 0.3], [0.2, 0.5, 0.3], [0.3, 0.3, 0.4]], np.float32)
check("無門檻：取機率最高的類別", MM.decide(pp, None).tolist() == [0, 1, 2])
check("門檻 0.3：危險機率 >= 0.3 就喊危險", MM.decide(pp, 0.3).tolist() == [2, 2, 2])
check("門檻 0.5：不到門檻時在安全/注意中取高者", MM.decide(pp, 0.5).tolist() == [0, 1, 0])

print(f"\n{'='*58}\n通過 {len(PASS)} 項，失敗 {len(FAIL)} 項")
if FAIL:
    for f in FAIL:
        print(f"  FAILED: {f}")
    sys.exit(1)
print("全部通過")
