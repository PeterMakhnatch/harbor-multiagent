"""Verbatim notes from digest playtest rounds 4 and 5, with the tester's labels.

References come from each built task's rule_heads, not from the submitted notes.
Both reports label all notes as preserving the manual's numbers and order.
"""

import pytest
from conftest import FLOTILLA, load

EXT = load(FLOTILLA / "world" / "ext" / "digest.py")

NOTES = [
    pytest.param([[32, 'rush'], [74, 'account'], [77, 'stock'], [14, 'proof']], (True, True),
                 """Home LB=N FL=W CA=W MB=S.
First matching main in order; deepest nested exception overrides action/check. Replace same # in place. Default home.
32 rush=yes qty>4500: press free home; busy hold (CA busy S).
74 account=new: credit ok home, stop E; qty<80 instead E (delivery N), no check.
77 stock=custom: hold; MB instead F.
14 proof=pending: N; key & rush=no C, within that qty>9000 S.""",
                 id="r4-so-team-i0-codex-p1"),
    pytest.param([[32, 'rush'], [74, 'account'], [77, 'stock'], [14, 'proof']], (True, True),
                 """Home LB=N FL=W CA=W MB=S.
First matching main in order; deepest nested exception overrides action/check. Replace same # in place. Default home.
32 rush=yes qty>4500: press free home; busy hold (CA busy S).
74 account=new: credit ok home, stop E; qty<80 instead E (delivery Q), no check.
77 stock=custom: hold; MB instead F.
14 proof=pending: N; key & rush=no C, within that qty>9000 S.""",
                 id="r4-so-team-i0-codex-p2"),
    pytest.param([[32, 'rush'], [74, 'account'], [77, 'stock'], [14, 'proof']], (True, True),
                 """Home LB=N FL=W CA=W MB=S.
First matching main in order; deepest nested exception overrides action/check. Replace same # in place. Default home.
32 rush=yes qty>4500: press free home, busy hold; CA instead C no check.
74 account=new: credit ok home, stop E; qty<80 instead E (delivery Q), no check.
77 stock=custom: hold; MB instead F.
14 proof=pending: N; key & rush=no C, within that qty>9000 S.""",
                 id="r4-so-team-i0-codex-p3"),
    pytest.param([[32, 'rush'], [74, 'account'], [77, 'stock'], [14, 'proof']], (True, True),
                 """Home: LB=N FL=W CA=W MB=S. First matching rule in order; deepest exception wins. None match->home.
1 R32 rush=yes&qty>4500: chk press; free->home, busy->hold. If CA: busy->S
2 R74 acct=new: chk credit; ok->home, stop->E. If qty<80: E (no chk); +ship=delivery: N
3 R77 stock=custom: hold. If MB: F
4 R14 proof=pending: N. If key&rush=no: C; +qty>9000: S
Your replacements keep rule's slot.
""",
                 id="r4-so-team-i0-claude-p1"),
    pytest.param([[32, 'rush'], [74, 'account'], [77, 'stock'], [14, 'proof']], (True, True),
                 """Home: LB=N FL=W CA=W MB=S. First matching rule in order; deepest exception wins. None match->home.
1 R32 rush=yes&qty>4500: chk press; free->home, busy->hold. If CA: busy->S
2 R74 acct=new: chk credit; ok->home, stop->E. If qty<80: E (no chk); +ship=delivery: Q
3 R77 stock=custom: hold. If MB: F
4 R14 proof=pending: N. If key&rush=no: C; +qty>9000: S
Your replacements keep rule's slot.
""",
                 id="r4-so-team-i0-claude-p2"),
    pytest.param([[32, 'rush'], [74, 'account'], [77, 'stock'], [14, 'proof']], (True, True),
                 """Home: LB=N FL=W CA=W MB=S. First matching rule in order; deepest exception wins. None match->home.
1 R32 rush=yes&qty>4500: chk press; free->home, busy->hold. If CA: C (no chk)
2 R74 acct=new: chk credit; ok->home, stop->E. If qty<80: E (no chk); +ship=delivery: Q
3 R77 stock=custom: hold. If MB: F
4 R14 proof=pending: N. If key&rush=no: C; +qty>9000: S
Your replacements keep rule's slot.
""",
                 id="r4-so-team-i0-claude-p3"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """First matching rule in order; nested exceptions override parent; later same-number rule replaces in place. H=home: LB S, EN/MB E, FL N. Default H.
29 new: qty<120 -> X (courier -> hold); otherwise check credit ok H/stop X.
47 rush yes qty>6000: check press free H/busy S (MB busy C).
23 proof pending: hold except key & rush no -> P (qty>5000 -> C).
83 bind perfect -> Q.
20 stock custom -> S (FL -> E).""",
                 id="r4-ho-team-i0-codex-p1"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """First matching rule in order; nested exceptions override parent; later same-number rule replaces in place. H=home: LB S, EN/MB E, FL N. Default H; unknown H hold.
29 new: qty<120 -> X (courier -> hold); otherwise check credit ok H/stop X.
47 rush yes qty>6000: check press free H/busy S (MB busy C).
23 proof pending: hold except key & rush no -> P (qty>5000 -> C).
83 bind perfect -> Q.
20 stock custom -> S (FL -> F).""",
                 id="r4-ho-team-i0-codex-p2"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """First matching rule in order; nested exceptions override parent; later same-number rule replaces in place. H=home: LB S, EN/MB E, FL N. Default H; unknown H hold.
29 new: qty<120 -> X (courier -> C); otherwise check credit ok H/stop X.
47 rush yes qty>6000: check press free H/busy S (MB busy C).
23 proof pending: hold except key & rush no -> P (qty>5000 -> C).
83 bind perfect -> Q.
20 stock custom -> S (FL -> F).""",
                 id="r4-ho-team-i0-codex-p3"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """First matching rule in order; nested exceptions override parent; later same-number rule replaces in place. H=home: LB S, EN/MB E, FL N. Default H; unknown H hold.
29 new: qty<120 -> X (courier -> C); otherwise check credit ok H/stop X.
47 rush yes qty>6000: check press free H/busy S (MB busy C).
23 proof pending: hold except key & rush no -> P (qty>5000 -> Q).
83 bind perfect -> Q.
20 stock custom -> S (FL -> F).""",
                 id="r4-ho-team-i0-codex-p4"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """Home: LB=S EN=E MB=E FL=N. First match wins; deepest exception replaces. No match->home. Your replacements override same-number rule in place.
29 acct new: chk credit ok->home stop->X
 qty<120: X
  ship courier: hold
47 rush yes & qty>6000: chk press free->home busy->S
 prod MB: chk press free->home busy->C
23 proof pending: hold
 acct key & rush no: P
  qty>5000: C
83 bind perfect: Q
20 stock custom: S
 prod FL: E
""",
                 id="r4-ho-team-i0-claude-p1"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """Home: LB=S EN=E MB=E FL=N. First match wins; deepest exception replaces. No match->home. Your replacements override same-number rule in place.
29 acct new: chk credit ok->home stop->X
 qty<120: X
  ship courier: hold
47 rush yes & qty>6000: chk press free->home busy->S
 prod MB: chk press free->home busy->C
23 proof pending: hold
 acct key & rush no: P
  qty>5000: C
83 bind perfect: Q
20 stock custom: S
 prod FL: F
""",
                 id="r4-ho-team-i0-claude-p2"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """Home: LB=S EN=E MB=E FL=N. First match wins; deepest exception replaces. No match->home. Your replacements override same-number rule in place.
29 acct new: chk credit ok->home stop->X
 qty<120: X
  ship courier: C
47 rush yes & qty>6000: chk press free->home busy->S
 prod MB: chk press free->home busy->C
23 proof pending: hold
 acct key & rush no: P
  qty>5000: C
83 bind perfect: Q
20 stock custom: S
 prod FL: F
""",
                 id="r4-ho-team-i0-claude-p3"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """Home: LB=S EN=E MB=E FL=N. First match wins; deepest exception replaces. No match->home. Your replacements override same-number rule in place.
29 acct new: chk credit ok->home stop->X
 qty<120: X
  ship courier: C
47 rush yes & qty>6000: chk press free->home busy->S
 prod MB: chk press free->home busy->C
23 proof pending: hold
 acct key & rush no: P
  qty>5000: Q
83 bind perfect: Q
20 stock custom: S
 prod FL: F
""",
                 id="r4-ho-team-i0-claude-p4"),
    pytest.param([[44, 'account'], [26, 'stock'], [95, 'proof'], [38, 'rush']], (True, True),
                 """First matching rule below; deepest matching exception overrides action/check. Later same-number replacement keeps slot. Home EN/LB=E, FL/PO=S; default home.
44 new: credit ok=>home, stop=>E; qty<100=>E instead, if also courier=>C.
26 custom=>N; PO=>P.
95 pending proof=>C; key & rush=no=>W; also qty>8000=>Q.
38 rush=yes & qty>4000: press free=>home, busy=>N (FL busy=>Q).""",
                 id="r4-so-team-i1-codex-p1"),
    pytest.param([[44, 'account'], [26, 'stock'], [95, 'proof'], [38, 'rush']], (True, True),
                 """First matching rule; deepest exception overrides action/check. Same-number replacement keeps slot. Home EN/LB=E, FL/PO=S; default home, unknown hold.
44 new: credit ok=>home, stop=>E; qty<100=>E instead, if also courier=>C.
26 custom=>N; PO=>P.
95 pending proof=>C; key & rush=no=>W; also qty>8000=>Q.
38 rush=yes & qty>4000: press free=>home, busy=>N; FL=>F instead (no check).""",
                 id="r4-so-team-i1-codex-p2"),
    pytest.param([[44, 'account'], [26, 'stock'], [95, 'proof'], [38, 'rush']], (True, True),
                 """First matching rule; deepest exception overrides action/check. Same-number replacement keeps slot. Home EN/LB=E, FL/PO=S; default home, unknown hold.
44 new: credit ok=>home, stop=>E; qty<100=>E instead, if also courier=>F.
26 custom=>N; PO=>P.
95 pending proof=>C; key & rush=no=>W; also qty>8000=>Q.
38 rush=yes & qty>4000: press free=>home, busy=>N; FL=>F instead (no check).""",
                 id="r4-so-team-i1-codex-p3"),
    pytest.param([[44, 'account'], [26, 'stock'], [95, 'proof'], [38, 'rush']], (True, True),
                 """Home: EN,LB=E; FL,PO=S. Use 1st matching rule in order; deepest exception wins. None=home.
R44 acct new: chk credit ok=home stop=E. qty<100: E. +ship courier: C
R26 stock custom: N. PO: P
R95 proof pending: C. key&rush no: W. +qty>8000: Q
R38 rush yes&qty>4000: chk press free=home busy=N. FL: chk press free=home busy=Q
New rule w/ same number replaces old, keeps its slot.
""",
                 id="r4-so-team-i1-claude-p1"),
    pytest.param([[44, 'account'], [26, 'stock'], [95, 'proof'], [38, 'rush']], (True, True),
                 """Home: EN,LB=E; FL,PO=S. Use 1st matching rule in order; deepest exception wins. None=home.
R44 acct new: chk credit ok=home stop=E. qty<100: E. +ship courier: C
R26 stock custom: N. PO: P
R95 proof pending: C. key&rush no: W. +qty>8000: Q
R38 rush yes&qty>4000: chk press free=home busy=N. FL: F (no chk)
New rule w/ same number replaces old, keeps its slot.
""",
                 id="r4-so-team-i1-claude-p2"),
    pytest.param([[44, 'account'], [26, 'stock'], [95, 'proof'], [38, 'rush']], (True, True),
                 """Home: EN,LB=E; FL,PO=S. Use 1st matching rule in order; deepest exception wins. None=home.
R44 acct new: chk credit ok=home stop=E. qty<100: E. +ship courier: F
R26 stock custom: N. PO: P
R95 proof pending: C. key&rush no: W. +qty>8000: Q
R38 rush yes&qty>4000: chk press free=home busy=N. FL: F (no chk)
New rule w/ same number replaces old, keeps its slot.
""",
                 id="r4-so-team-i1-claude-p3"),
    pytest.param([[29, 'account'], [20, 'stock'], [14, 'rush'], [38, 'proof'], [74, 'bind']], (True, True),
                 """First matching main wins; deepest matching exception overrides action/checks. Replace same-number rule in place. Home CA=W BK=N BC=S FL=N; default home. Ordered:
29 new: check credit ok home/stop X; qty<120 X, but courier W.
20 custom: N; FL hold.
14 rush=yes qty>2500: check press free home/busy N (BC busy C).
38 proof=pending: W; key & rush=no Q, but qty>11000 C.
74 bind=perfect: P.
Pass updated rules/order/home onward.""",
                 id="r4-ho-team-i1-codex-p1"),
    pytest.param([[29, 'account'], [20, 'stock'], [14, 'rush'], [38, 'proof'], [74, 'bind']], (True, True),
                 """First matching main wins; deepest matching exception overrides action/checks. Replace same-number rule in place. Home CA=W BK=N BC=S FL=N; default home, unknown hold. Ordered:
29 new: check credit ok home/stop X; qty<120 X, but courier W.
20 custom: N; FL hold.
14 rush=yes qty>2500: check press free home/busy N (BC busy C).
38 proof=pending: W; key & rush=no Q, but qty>11000 C.
74 bind=perfect: F.
Pass updated rules/order/home onward.""",
                 id="r4-ho-team-i1-codex-p2"),
    pytest.param([[29, 'account'], [20, 'stock'], [14, 'rush'], [38, 'proof'], [74, 'bind']], (True, True),
                 """First matching main wins; deepest matching exception overrides action/checks. Replace same-number rule in place. Home CA=W BK=N BC=S FL=N; default home, unknown hold. Ordered:
29 new: check credit ok home/stop X; qty<120 X, but courier W.
20 custom: N; FL E.
14 rush=yes qty>2500: check press free home/busy N (BC busy C).
38 proof=pending: W; key & rush=no Q, but qty>11000 C.
74 bind=perfect: F.
Pass updated rules/order/home onward.""",
                 id="r4-ho-team-i1-codex-p3"),
    pytest.param([[29, 'account'], [20, 'stock'], [14, 'rush'], [38, 'proof'], [74, 'bind']], (True, True),
                 """First matching main wins; deepest matching exception overrides action/checks. Replace same-number rule in place. Home CA=W BK=N BC=S FL=N; default home, unknown hold. Ordered:
29 new: check credit ok home/stop X; qty<120 X, but courier P.
20 custom: N; FL E.
14 rush=yes qty>2500: check press free home/busy N (BC busy C).
38 proof=pending: W; key & rush=no Q, but qty>11000 C.
74 bind=perfect: F.
Pass updated rules/order/home onward.""",
                 id="r4-ho-team-i1-codex-p4"),
    pytest.param([[29, 'account'], [20, 'stock'], [14, 'rush'], [38, 'proof'], [74, 'bind']], (True, True),
                 """Home: CA=W BK=N BC=S FL=N. First match in order; deepest exception wins. None->home.
R29 acct new: chk credit ok=home stop=X
 qty<120: X
  +ship courier: W
R20 stock custom: N
 FL: hold
R14 rush yes & qty>2500: chk press free=home busy=N
 BC: chk press free=home busy=C
R38 proof pending: W
 acct key & rush no: Q
  +qty>11000: C
R74 bind perfect: P
Your replacements: same-number rule replaces in place.
""",
                 id="r4-ho-team-i1-claude-p1"),
    pytest.param([[29, 'account'], [20, 'stock'], [14, 'rush'], [38, 'proof'], [74, 'bind']], (True, True),
                 """Home: CA=W BK=N BC=S FL=N. First match in order; deepest exception wins. None->home.
R29 acct new: chk credit ok=home stop=X
 qty<120: X
  +ship courier: W
R20 stock custom: N
 FL: hold
R14 rush yes & qty>2500: chk press free=home busy=N
 BC: chk press free=home busy=C
R38 proof pending: W
 acct key & rush no: Q
  +qty>11000: C
R74 bind perfect: F
Your replacements: same-number rule replaces in place.
""",
                 id="r4-ho-team-i1-claude-p2"),
    pytest.param([[29, 'account'], [20, 'stock'], [14, 'rush'], [38, 'proof'], [74, 'bind']], (True, True),
                 """Home: CA=W BK=N BC=S FL=N. First match in order; deepest exception wins. None->home.
R29 acct new: chk credit ok=home stop=X
 qty<120: X
  +ship courier: W
R20 stock custom: N
 FL: E
R14 rush yes & qty>2500: chk press free=home busy=N
 BC: chk press free=home busy=C
R38 proof pending: W
 acct key & rush no: Q
  +qty>11000: C
R74 bind perfect: F
Your replacements: same-number rule replaces in place.
""",
                 id="r4-ho-team-i1-claude-p3"),
    pytest.param([[29, 'account'], [20, 'stock'], [14, 'rush'], [38, 'proof'], [74, 'bind']], (True, True),
                 """Home: CA=W BK=N BC=S FL=N. First match in order; deepest exception wins. None->home.
R29 acct new: chk credit ok=home stop=X
 qty<120: X
  +ship courier: P
R20 stock custom: N
 FL: E
R14 rush yes & qty>2500: chk press free=home busy=N
 BC: chk press free=home busy=C
R38 proof pending: W
 acct key & rush no: Q
  +qty>11000: C
R74 bind perfect: F
Your replacements: same-number rule replaces in place.
""",
                 id="r4-ho-team-i1-claude-p4"),
    pytest.param([[62, 'rush'], [65, 'stock'], [23, 'account'], [71, 'proof']], (True, True),
                 """First matching rule wins; deepest exception overrides action/check. Replacements retain slot. Home BC/MB/BK=S, EN=W. Ordered:
62 rush=yes qty>6500: check press free=home busy=Q; BK busy=F.
65 stock=custom: Q; EN=S.
23 account=new: check credit ok=home stop=N; qty<140=N (courier=E), no check.
71 proof=pending: E; key+nonrush=hold (qty>7000=F).
Else home.""",
                 id="r4-so-team-i2-codex-p1"),
    pytest.param([[62, 'rush'], [65, 'stock'], [23, 'account'], [71, 'proof']], (True, True),
                 """First matching rule wins; deepest exception overrides action/check. Replacements retain slot. Home BC/MB/BK=S, EN=W. Ordered:
62 rush=yes qty>6500: check press free=home busy=Q; BK=X no check.
65 stock=custom: Q; EN=S.
23 account=new: check credit ok=home stop=N; qty<140=N (courier=E), no check.
71 proof=pending: E; key+nonrush=hold (qty>7000=F).
Else home, unknown hold.""",
                 id="r4-so-team-i2-codex-p2"),
    pytest.param([[62, 'rush'], [65, 'stock'], [23, 'account'], [71, 'proof']], (True, True),
                 """First matching rule wins; deepest exception overrides action/check. Replacements retain slot. Home BC/MB/BK=S, EN=W. Ordered:
62 rush=yes qty>6500: check press free=home busy=Q; BK=X no check.
65 stock=custom: Q; EN=F.
23 account=new: check credit ok=home stop=N; qty<140=N (courier=E), no check.
71 proof=pending: E; key+nonrush=hold (qty>7000=F).
Else home, unknown hold.""",
                 id="r4-so-team-i2-codex-p3"),
    pytest.param([[62, 'rush'], [65, 'stock'], [23, 'account'], [71, 'proof']], (True, True),
                 """Home: BC,MB,BK=S EN=W. First match in order; deepest exception wins. None->home.
62 rush=yes&qty>6500: chk press free->home busy->Q
 BK: chk press free->home busy->F
65 stock=custom: Q
 EN: S
23 acct=new: chk credit ok->home stop->N
 qty<140: N
  ship=courier: E
71 proof=pending: E
 acct=key&rush=no: hold
  qty>7000: F
Your replacements: same rule number replaces it in its place.
""",
                 id="r4-so-team-i2-claude-p1"),
    pytest.param([[62, 'rush'], [65, 'stock'], [23, 'account'], [71, 'proof']], (True, True),
                 """Home: BC,MB,BK=S EN=W. First match in order; deepest exception wins. None->home.
62 rush=yes&qty>6500: chk press free->home busy->Q
 BK: X
65 stock=custom: Q
 EN: S
23 acct=new: chk credit ok->home stop->N
 qty<140: N
  ship=courier: E
71 proof=pending: E
 acct=key&rush=no: hold
  qty>7000: F
Your replacements: same rule number replaces it in its place.
""",
                 id="r4-so-team-i2-claude-p2"),
    pytest.param([[62, 'rush'], [65, 'stock'], [23, 'account'], [71, 'proof']], (True, True),
                 """Home: BC,MB,BK=S EN=W. First match in order; deepest exception wins. None->home.
62 rush=yes&qty>6500: chk press free->home busy->Q
 BK: X
65 stock=custom: Q
 EN: F
23 acct=new: chk credit ok->home stop->N
 qty<140: N
  ship=courier: E
71 proof=pending: E
 acct=key&rush=no: hold
  qty>7000: F
Your replacements: same rule number replaces it in its place.
""",
                 id="r4-so-team-i2-claude-p3"),
    pytest.param([[17, 'proof'], [98, 'stock'], [32, 'account'], [89, 'rush'], [71, 'bind']], (True, True),
                 """Home BK,EN=E; BC,CA=W. First matching rule in order; deepest matching exception overrides action/checks; later same-number rule replaces whole rule in place. Default home.
17 pending proof:X; if key & not rush:E, within that qty>12000:P.
98 custom stock:F; CA:S.
32 new account:credit ok=home stop=N; qty<50:N no check, within that delivery:X.
89 rush yes & qty>3500:press free=home busy=F; EN busy=P (still press).
71 perfect bind:hold.""",
                 id="r4-ho-team-i2-codex-p1"),
    pytest.param([[17, 'proof'], [98, 'stock'], [32, 'account'], [89, 'rush'], [71, 'bind']], (True, True),
                 """Home BK,EN=E; BC,CA=W. First matching rule in order; deepest matching exception overrides action/checks; later same-number rule replaces whole rule in place. Default home.
17 pending proof:X; if key & not rush:E, within that qty>12000:C.
98 custom stock:F; CA:S.
32 new account:credit ok=home stop=N; qty<50:N no check, within that delivery:X.
89 rush yes & qty>3500:press free=home busy=F; EN busy=P (still press).
71 perfect bind:hold.""",
                 id="r4-ho-team-i2-codex-p2"),
    pytest.param([[17, 'proof'], [98, 'stock'], [32, 'account'], [89, 'rush'], [71, 'bind']], (True, True),
                 """Home BK,EN=E; BC,CA=W. First matching rule in order; deepest matching exception overrides action/checks; later same-number rule replaces whole rule in place. Default home, unknown hold.
17 pending proof:X; if key & not rush:E, within that qty>12000:C.
98 custom stock:F; CA:Q.
32 new account:credit ok=home stop=N; qty<50:N no check, within that delivery:X.
89 rush yes & qty>3500:press free=home busy=F; EN busy=P (still press).
71 perfect bind:hold.""",
                 id="r4-ho-team-i2-codex-p3"),
    pytest.param([[17, 'proof'], [98, 'stock'], [32, 'account'], [89, 'rush'], [71, 'bind']], (True, True),
                 """Home BK,EN=E; BC,CA=W. First matching rule in order; deepest matching exception overrides action/checks; later same-number rule replaces whole rule in place. Default home, unknown hold.
17 pending proof:X; if key & not rush:E, within that qty>12000:C.
98 custom stock:F; CA:Q.
32 new account:credit ok=home stop=N; qty<50:N no check, within that delivery:Q.
89 rush yes & qty>3500:press free=home busy=F; EN busy=P (still press).
71 perfect bind:hold.""",
                 id="r4-ho-team-i2-codex-p4"),
    pytest.param([[17, 'proof'], [98, 'stock'], [32, 'account'], [89, 'rush'], [71, 'bind']], (True, True),
                 """Home: BK,EN=E BC,CA=W. First match wins; deepest exception wins. None->home.
R17 proof pending:X; key&rush no:E; +qty>12000:P
R98 stock custom:F; CA:S
R32 acct new: chk credit ok=home stop=N; qty<50:N; +ship delivery:X
R89 rush yes&qty>3500: chk press free=home busy=F; EN: free=home busy=P
R71 bind perfect:hold
Order 17,98,32,89,71. Your replacement rule w/ same number takes that rule's place. Must do check before send.
""",
                 id="r4-ho-team-i2-claude-p1"),
    pytest.param([[17, 'proof'], [98, 'stock'], [32, 'account'], [89, 'rush'], [71, 'bind']], (True, True),
                 """Home: BK,EN=E BC,CA=W. First match wins; deepest exception wins. None->home.
R17 proof pending:X; key&rush no:E; +qty>12000:C
R98 stock custom:F; CA:S
R32 acct new: chk credit ok=home stop=N; qty<50:N; +ship delivery:X
R89 rush yes&qty>3500: chk press free=home busy=F; EN: free=home busy=P
R71 bind perfect:hold
Order 17,98,32,89,71. Your replacement rule w/ same number takes that rule's place. Must do check before send.
""",
                 id="r4-ho-team-i2-claude-p2"),
    pytest.param([[17, 'proof'], [98, 'stock'], [32, 'account'], [89, 'rush'], [71, 'bind']], (True, True),
                 """Home: BK,EN=E BC,CA=W. First match wins; deepest exception wins. None->home.
R17 proof pending:X; key&rush no:E; +qty>12000:C
R98 stock custom:F; CA:Q
R32 acct new: chk credit ok=home stop=N; qty<50:N; +ship delivery:X
R89 rush yes&qty>3500: chk press free=home busy=F; EN: free=home busy=P
R71 bind perfect:hold
Order 17,98,32,89,71. Your replacement rule w/ same number takes that rule's place. Must do check before send.
""",
                 id="r4-ho-team-i2-claude-p3"),
    pytest.param([[17, 'proof'], [98, 'stock'], [32, 'account'], [89, 'rush'], [71, 'bind']], (True, True),
                 """Home: BK,EN=E BC,CA=W. First match wins; deepest exception wins. None->home.
R17 proof pending:X; key&rush no:E; +qty>12000:C
R98 stock custom:F; CA:Q
R32 acct new: chk credit ok=home stop=N; qty<50:N; +ship delivery:Q
R89 rush yes&qty>3500: chk press free=home busy=F; EN: free=home busy=P
R71 bind perfect:hold
Order 17,98,32,89,71. Your replacement rule w/ same number takes that rule's place. Must do check before send.
""",
                 id="r4-ho-team-i2-claude-p4"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """Day planner completed all 4 jobs (all default home routing). Manual for next shift:
Home: LB=S, EN=E, MB=E, FL=N.
Use first matching main rule in this order. Descend to deepest matching exception; its action AND checks replace those of parent. Later complete rule with same number replaces earlier one in its original position. No match: send home.
29 NEW account: if qty <120, send X, except ship=courier -> HOLD. Otherwise check credit: ok -> home, stop -> X.
47 rush=yes AND qty >6000: check press; free -> home; busy -> S, except product=MB -> C.
23 proof=pending: HOLD, except account=key AND rush=no -> P; within that exception qty >5000 -> C.
83 bind=perfect: Q.
20 stock=custom: S, except product=FL -> E.
No checks required for other clauses. Thresholds strict. A required check must be made before action. Do not apply lower rules after matching a main rule. Actions permanent. Finish all jobs before handover. Follow any later rule replacements or additions in your shift instructions.""",
                 id="r4-ho-full-i0-codex-p1"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """Completed all 4 relay jobs (MB/EN, all default home lane E). Effective rules for next shift:
Home: LB=S, EN=E, MB=E, FL=N.
Use first matching main rule in this order. Descend to deepest matching exception; its action AND checks replace those of parent. Later complete rule with same number replaces earlier one in its original position. No match: send home; if home unknown, hold.
29 account=new: if qty <120, send X, except ship=courier -> HOLD. Otherwise check credit: ok -> home, stop -> X.
47 rush=yes AND qty >6000: check press; free -> home; busy -> S, except product=MB -> C.
23 proof=pending: HOLD, except account=key AND rush=no -> P; within that exception qty >5000 -> C.
83 bind=perfect: Q.
20 stock=custom: S, except product=FL -> F (today's replacement; no longer E).
No checks required for other clauses. Thresholds strict. Make required checks before final action. Do not apply lower rules after matching a main rule. Actions permanent. Finish all jobs before handover. Apply subsequent replacements or additions in your shift instructions.""",
                 id="r4-ho-full-i0-codex-p2"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """Completed all 4 relay jobs: LB -> S (2), MB -> E (1), EN -> E (1), all default home routing. Queue done.
Effective rules for next shift:
Home lanes: LB=S, EN=E, MB=E, FL=N.
Use first matching main rule in the order below. Descend to deepest matching exception; its action AND checks replace those of its parent. A later complete replacement keeps the original rule position. No matching rule: send home; unknown home: hold.
29 account=new: if qty <120, send X, except ship=courier -> C (latest replacement, NOT hold). Otherwise check credit: ok -> home, stop -> X.
47 rush=yes AND qty >6000: check press; free -> home; busy -> S, except product=MB -> C when busy (press check still required).
23 proof=pending: HOLD, except account=key AND rush=no -> P; within that exception qty >5000 -> C.
83 bind=perfect: Q.
20 stock=custom: S, except product=FL -> F (earlier replacement, NOT E).
No checks required for other clauses. Thresholds are strict. Make all required checks before final action. Do not apply lower rules after matching a main rule. Actions permanent. Finish all jobs before sealing. Apply any subsequent replacements in your shift instructions.""",
                 id="r4-ho-full-i0-codex-p3"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """Completed all 4 relay jobs: MB and EN -> E, two LB -> S, all default home routing. Queue confirmed done.
Effective rules for next shift:
Home lanes: LB=S, EN=E, MB=E, FL=N.
Use first matching MAIN rule in the order below. Descend to deepest matching exception; conditions accumulate and its action AND checks replace its parent's. Later complete replacements keep their original position. No match: send home; unknown home: hold.
29 account=new: if qty <120, send X, except ship=courier -> C (not hold). Otherwise check credit: ok -> home, stop -> X.
47 rush=yes AND qty >6000: check press; free -> home; busy -> S, except product=MB -> C when busy (press check still required).
23 proof=pending: HOLD, except account=key AND rush=no -> P; within that exception qty >5000 -> Q (TODAY'S replacement, no longer C).
83 bind=perfect: Q.
20 stock=custom: S, except product=FL -> F (earlier replacement, not E).
No checks required for other clauses. Thresholds strict. Make required checks before final actions. Do not apply lower rules after matching a main rule. Actions permanent. Finish all jobs before sealing. Apply any subsequent replacements in your shift instructions.""",
                 id="r4-ho-full-i0-codex-p4"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """HANDOVER - Harbor Print finishing desk (full routing rules, this edition)
Home lanes: LB=S, EN=E, MB=E, FL=N. Other products: home lane unknown to me.
Apply the FIRST matching main rule in this order. Within a rule, the deepest matching exception wins (replaces action and checks). If no rule matches, send home.

1) Rule 29: account=new
   - qty<120 AND ship=courier -> HOLD
   - qty<120 (other ship)     -> send X (no check needed)
   - else: CHECK credit; ok -> home, stop -> X
2) Rule 47: rush=yes AND qty>6000
   - product=MB: CHECK press; free -> home (E), busy -> C
   - else: CHECK press; free -> home, busy -> S
3) Rule 23: proof=pending
   - account=key AND rush=no AND qty>5000 -> send C
   - account=key AND rush=no               -> send P
   - else -> HOLD
4) Rule 83: bind=perfect -> send Q
5) Rule 20: stock=custom
   - product=FL -> send E
   - else -> send S
6) Otherwise -> send home.

Notes: qty "over"/"under" are strict (>, <). A correct lane without its required check counts wrong - always do the check first for Rule 29 main and Rule 47. Extra checks are harmless. Actions are permanent.
My shift: 4 jobs, all standard/approved/house/stapled, none matched a rule -> sent home (3x EN->E, 1x LB->S).
""",
                 id="r4-ho-full-i0-claude-p1"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """HANDOVER - Harbor Print finishing desk (full routing rules, this edition, as of shift p2)
Home lanes: LB=S, EN=E, MB=E, FL=N. Other products: home lane unknown -> HOLD if no rule applies.
Apply the FIRST matching main rule in this order. Within a rule, the deepest matching exception wins (replaces action and checks). If no rule matches, send home.

1) Rule 29: account=new
   - qty<120 AND ship=courier -> HOLD
   - qty<120 (other ship)     -> send X (no check needed)
   - else: CHECK credit; ok -> home, stop -> X
2) Rule 47: rush=yes AND qty>6000
   - product=MB: CHECK press; free -> home (E), busy -> C
   - else: CHECK press; free -> home, busy -> S
3) Rule 23: proof=pending
   - account=key AND rush=no AND qty>5000 -> send C
   - account=key AND rush=no               -> send P
   - else -> HOLD
4) Rule 83: bind=perfect -> send Q
5) Rule 20 (REPLACED in shift p2, keeps its position): stock=custom
   - product=FL -> send F   (CHANGED: was E)
   - else -> send S
6) Otherwise -> send home.

Notes: qty "over"/"under" are strict (>, <). A correct lane without its required check counts wrong - always do the check first for Rule 29 main and Rule 47. Extra checks are harmless. Actions are permanent.
Shift p1: 4 jobs, no rule matched -> home.
Shift p2: 4 jobs (2x MB, 2x EN, all standard/approved/house/stapled/no rush), no rule matched -> all sent home E.
""",
                 id="r4-ho-full-i0-claude-p2"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """HANDOVER - Harbor Print finishing desk (full routing rules, this edition, as of shift p3)
Home lanes: LB=S, EN=E, MB=E, FL=N. Other products: home lane unknown -> HOLD if no rule applies.
Apply the FIRST matching main rule in this order. Within a rule, the deepest matching exception wins (replaces action and checks). If no rule matches, send home.

1) Rule 29 (REPLACED in shift p3, keeps its position): account=new
   - qty<120 AND ship=courier -> send C   (CHANGED: was HOLD)
   - qty<120 (other ship)     -> send X (no check needed)
   - else: CHECK credit; ok -> home, stop -> X
2) Rule 47: rush=yes AND qty>6000
   - product=MB: CHECK press; free -> home (E), busy -> C
   - else: CHECK press; free -> home, busy -> S
3) Rule 23: proof=pending
   - account=key AND rush=no AND qty>5000 -> send C
   - account=key AND rush=no               -> send P
   - else -> HOLD
4) Rule 83: bind=perfect -> send Q
5) Rule 20 (REPLACED in shift p2, keeps its position): stock=custom
   - product=FL -> send F   (was E in original manual)
   - else -> send S
6) Otherwise -> send home.

Notes: qty "over"/"under" are strict (>, <). A correct lane without its required check counts wrong - always do the check first for Rule 29 main and Rule 47. Extra checks are harmless. Actions are permanent.
Shift p1: 4 jobs, no rule matched -> home.
Shift p2: 4 jobs, no rule matched -> all sent home E.
Shift p3: 4 jobs (2x LB, 1x MB, 1x EN, all standard/approved/house/stapled/no rush), no rule matched -> LB->S, MB->E, EN->E.
""",
                 id="r4-ho-full-i0-claude-p3"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """HANDOVER - Harbor Print finishing desk (full routing rules, this edition, as of shift p4)
Home lanes: LB=S, EN=E, MB=E, FL=N. Other products: home lane unknown -> HOLD if no rule applies.
Apply the FIRST matching main rule in this order. Within a rule, the deepest matching exception wins (replaces action and checks). If no rule matches, send home.

1) Rule 29 (REPLACED in shift p3, keeps its position): account=new
   - qty<120 AND ship=courier -> send C   (was HOLD in original manual)
   - qty<120 (other ship)     -> send X (no check needed)
   - else: CHECK credit; ok -> home, stop -> X
2) Rule 47: rush=yes AND qty>6000
   - product=MB: CHECK press; free -> home (E), busy -> C
   - else: CHECK press; free -> home, busy -> S
3) Rule 23 (REPLACED in shift p4, keeps its position): proof=pending
   - account=key AND rush=no AND qty>5000 -> send Q   (CHANGED: was C)
   - account=key AND rush=no               -> send P
   - else -> HOLD
4) Rule 83: bind=perfect -> send Q
5) Rule 20 (REPLACED in shift p2, keeps its position): stock=custom
   - product=FL -> send F   (was E in original manual)
   - else -> send S
6) Otherwise -> send home.

Notes: qty "over"/"under" are strict (>, <). A correct lane without its required check counts wrong - always do the check first for Rule 29 main and Rule 47. Extra checks are harmless. Actions are permanent.
Shift p1: 4 jobs, no rule matched -> home.
Shift p2: 4 jobs, no rule matched -> all sent home E.
Shift p3: 4 jobs, no rule matched -> LB->S, MB->E, EN->E.
Shift p4: 4 jobs (1x MB, 2x LB, 1x EN, all standard/approved/house/stapled/no rush), no rule matched -> MB->E, LB->S, EN->E.
""",
                 id="r4-ho-full-i0-claude-p4"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """First matching main; indented exceptions add to parent, deepest wins (replaces checks/action). Home H: LB=S EN=E MB=E FL=N. Default H.
29 account=new: credit ok H/stop X
 qty<120: X
  ship=courier: HOLD
47 rush=yes & qty>6000: press free H/busy S
 product=MB: press free H/busy C
23 proof=pending: HOLD
 account=key & rush=no: P
  qty>5000: C
83 bind=perfect: Q
20 stock=custom: S
 product=FL: E""",
                 id="r4-ho-hint-i0-codex-p1"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """First matching main; indented exceptions add to parent, deepest wins (replaces checks/action). Home H: LB=S EN=E MB=E FL=N. Default H.
29 account=new: credit ok H/stop X
 qty<120: X
  ship=courier: HOLD
47 rush=yes & qty>6000: press free H/busy S
 product=MB: press free H/busy C
23 proof=pending: HOLD
 account=key & rush=no: P
  qty>5000: C
83 bind=perfect: Q
20 stock=custom: S
 product=FL: F""",
                 id="r4-ho-hint-i0-codex-p2"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """First matching main; indented exceptions add to parent, deepest wins (replaces checks/action). Home H: LB=S EN=E MB=E FL=N. Default H.
29 account=new: credit ok H/stop X
 qty<120: X
  ship=courier: C
47 rush=yes & qty>6000: press free H/busy S
 product=MB: press free H/busy C
23 proof=pending: HOLD
 account=key & rush=no: P
  qty>5000: C
83 bind=perfect: Q
20 stock=custom: S
 product=FL: F""",
                 id="r4-ho-hint-i0-codex-p3"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """First matching main; indented exceptions add to parent, deepest wins (replaces checks/action). Home H: LB=S EN=E MB=E FL=N. Default H; unknown H hold.
29 account=new: credit ok H/stop X
 qty<120: X
  ship=courier: C
47 rush=yes & qty>6000: press free H/busy S
 product=MB: press free H/busy C
23 proof=pending: HOLD
 account=key & rush=no: P
  qty>5000: Q
83 bind=perfect: Q
20 stock=custom: S
 product=FL: F""",
                 id="r4-ho-hint-i0-codex-p4"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """Home: LB=S EN=E MB=E FL=N. First matching rule in order; deepest exception wins. None->home.
29 acct new: chk credit ok->home stop->X
 qty<120: X
  ship courier: hold
47 rush yes & qty>6000: chk press free->home busy->S
 prod MB: chk press free->home busy->C
23 proof pending: hold
 acct key & rush no: P
  qty>5000: C
83 bind perfect: Q
20 stock custom: S
 prod FL: E
Apply your own replacements in place (same rule number keeps its position).
""",
                 id="r4-ho-hint-i0-claude-p1"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """Home: LB=S EN=E MB=E FL=N. First matching rule in order; deepest exception wins. None->home.
29 acct new: chk credit ok->home stop->X
 qty<120: X
  ship courier: hold
47 rush yes & qty>6000: chk press free->home busy->S
 prod MB: chk press free->home busy->C
23 proof pending: hold
 acct key & rush no: P
  qty>5000: C
83 bind perfect: Q
20 stock custom: S
 prod FL: F
Apply your own replacements in place (same rule number keeps its position).
""",
                 id="r4-ho-hint-i0-claude-p2"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """Home: LB=S EN=E MB=E FL=N. First matching rule in order; deepest exception wins. None->home.
29 acct new: chk credit ok->home stop->X
 qty<120: X
  ship courier: C
47 rush yes & qty>6000: chk press free->home busy->S
 prod MB: chk press free->home busy->C
23 proof pending: hold
 acct key & rush no: P
  qty>5000: C
83 bind perfect: Q
20 stock custom: S
 prod FL: F
Apply your own replacements in place (same rule number keeps its position).
""",
                 id="r4-ho-hint-i0-claude-p3"),
    pytest.param([[29, 'account'], [47, 'rush'], [23, 'proof'], [83, 'bind'], [20, 'stock']], (True, True),
                 """Home: LB=S EN=E MB=E FL=N. First matching rule in order; deepest exception wins. None->home.
29 acct new: chk credit ok->home stop->X
 qty<120: X
  ship courier: C
47 rush yes & qty>6000: chk press free->home busy->S
 prod MB: chk press free->home busy->C
23 proof pending: hold
 acct key & rush no: P
  qty>5000: Q
83 bind perfect: Q
20 stock custom: S
 prod FL: F
Apply your own replacements in place (same rule number keeps its position).
""",
                 id="r4-ho-hint-i0-claude-p4"),
    pytest.param([[29, 'account'], [20, 'stock'], [14, 'rush'], [38, 'proof'], [74, 'bind']], (True, True),
                 """First matching rule; nested exceptions add conditions, deepest replaces action/checks. H=home: CA W,BK N,BC S,FL N. Default H.
29 account=new: check credit ok H/stop X
  qty<120: X
    ship=courier: W
20 stock=custom: N
  product=FL: hold
14 rush=yes & qty>2500: check press free H/busy N
  product=BC: check press free H/busy C
38 proof=pending: W
  account=key & rush=no: Q
    qty>11000: C
74 bind=perfect: P""",
                 id="r4-ho-hint-i1-codex-p1"),
    pytest.param([[29, 'account'], [20, 'stock'], [14, 'rush'], [38, 'proof'], [74, 'bind']], (True, True),
                 """First matching rule; nested exceptions add conditions, deepest replaces action/checks. H=home: CA W,BK N,BC S,FL N. Default H.
29 account=new: check credit ok H/stop X
  qty<120: X
    ship=courier: W
20 stock=custom: N
  product=FL: hold
14 rush=yes & qty>2500: check press free H/busy N
  product=BC: check press free H/busy C
38 proof=pending: W
  account=key & rush=no: Q
    qty>11000: C
74 bind=perfect: F""",
                 id="r4-ho-hint-i1-codex-p2"),
    pytest.param([[29, 'account'], [20, 'stock'], [14, 'rush'], [38, 'proof'], [74, 'bind']], (True, True),
                 """First matching rule; nested exceptions add conditions, deepest replaces action/checks. H=home: CA W,BK N,BC S,FL N. Default H.
29 account=new: check credit ok H/stop X
  qty<120: X
    ship=courier: W
20 stock=custom: N
  product=FL: E
14 rush=yes & qty>2500: check press free H/busy N
  product=BC: check press free H/busy C
38 proof=pending: W
  account=key & rush=no: Q
    qty>11000: C
74 bind=perfect: F""",
                 id="r4-ho-hint-i1-codex-p3"),
    pytest.param([[29, 'account'], [20, 'stock'], [14, 'rush'], [38, 'proof'], [74, 'bind']], (True, True),
                 """First matching rule; nested exceptions add conditions, deepest replaces action/checks. H=home: CA W,BK N,BC S,FL N. Default H.
29 account=new: check credit ok H/stop X
  qty<120: X
    ship=courier: P
20 stock=custom: N
  product=FL: E
14 rush=yes & qty>2500: check press free H/busy N
  product=BC: check press free H/busy C
38 proof=pending: W
  account=key & rush=no: Q
    qty>11000: C
74 bind=perfect: F""",
                 id="r4-ho-hint-i1-codex-p4"),
    pytest.param([[29, 'account'], [20, 'stock'], [14, 'rush'], [38, 'proof'], [74, 'bind']], (True, True),
                 """First match in order; deepest exception wins. None->home.
Home: CA=W BK=N BC=S FL=N
29 account new: chk credit ok->home stop->X
 qty<120: X
  +ship courier: W
20 stock custom: N
 product FL: hold
14 rush yes & qty>2500: chk press free->home busy->N
 product BC: chk press free->home busy->C
38 proof pending: W
 account key & rush no: Q
  +qty>11000: C
74 bind perfect: P
A later rule with same number replaces it in place.
""",
                 id="r4-ho-hint-i1-claude-p1"),
    pytest.param([[29, 'account'], [20, 'stock'], [14, 'rush'], [38, 'proof'], [74, 'bind']], (True, True),
                 """First match in order; deepest exception wins. None->home.
Home: CA=W BK=N BC=S FL=N
29 account new: chk credit ok->home stop->X
 qty<120: X
  +ship courier: W
20 stock custom: N
 product FL: hold
14 rush yes & qty>2500: chk press free->home busy->N
 product BC: chk press free->home busy->C
38 proof pending: W
 account key & rush no: Q
  +qty>11000: C
74 bind perfect: F
A later rule with same number replaces it in place.
""",
                 id="r4-ho-hint-i1-claude-p2"),
    pytest.param([[29, 'account'], [20, 'stock'], [14, 'rush'], [38, 'proof'], [74, 'bind']], (True, True),
                 """First match in order; deepest exception wins. None->home.
Home: CA=W BK=N BC=S FL=N
29 account new: chk credit ok->home stop->X
 qty<120: X
  +ship courier: W
20 stock custom: N
 product FL: E
14 rush yes & qty>2500: chk press free->home busy->N
 product BC: chk press free->home busy->C
38 proof pending: W
 account key & rush no: Q
  +qty>11000: C
74 bind perfect: F
A later rule with same number replaces it in place.
""",
                 id="r4-ho-hint-i1-claude-p3"),
    pytest.param([[29, 'account'], [20, 'stock'], [14, 'rush'], [38, 'proof'], [74, 'bind']], (True, True),
                 """First match in order; deepest exception wins. None->home.
Home: CA=W BK=N BC=S FL=N
29 account new: chk credit ok->home stop->X
 qty<120: X
  +ship courier: P
20 stock custom: N
 product FL: E
14 rush yes & qty>2500: chk press free->home busy->N
 product BC: chk press free->home busy->C
38 proof pending: W
 account key & rush no: Q
  +qty>11000: C
74 bind perfect: F
A later rule with same number replaces it in place.
""",
                 id="r4-ho-hint-i1-claude-p4"),
    pytest.param([[77, 'stock'], [59, 'rush'], [23, 'account'], [74, 'proof']], (True, True),
                 """Home: PO W, MB S, CA W, BK W. No rule->home.
First matching rule in order; deepest exception wins. Your replacements keep the replaced rule's slot.
1) R77 stock custom: S. If BK: P.
2) R59 rush yes & qty>7000: chk press; free->home, busy->S. If CA: free->home, busy->C.
3) R23 acct new: chk credit; ok->home, stop->N. If qty<80: N; if also ship delivery: E.
4) R74 proof pending: E. If acct key & rush no: F; if also qty>11000: C.
Check result needed before send; extra checks OK.
""",
                 id="r5-so-impl-i0-claude-p1"),
    pytest.param([[77, 'stock'], [59, 'rush'], [23, 'account'], [74, 'proof']], (True, True),
                 """Home: PO W, MB S, CA W, BK W. No rule->home.
First matching rule in order; deepest exception wins. Your replacements keep the replaced rule's slot.
1) R77 stock custom: S. If BK: P.
2) R59 rush yes & qty>7000: chk press; free->home, busy->S. If CA: free->home, busy->C.
3) R23 acct new: chk credit; ok->home, stop->N. If qty<80: N; if also ship delivery: E.
4) R74 proof pending: E. If acct key & rush no: F; if also qty>11000: X.
Check result needed before send; extra checks OK.
""",
                 id="r5-so-impl-i0-claude-p2"),
    pytest.param([[77, 'stock'], [59, 'rush'], [23, 'account'], [74, 'proof']], (True, True),
                 """Home: PO W, MB S, CA W, BK W. No rule->home.
First matching rule in order; deepest exception wins. Your replacements keep the replaced rule's slot.
1) R77 stock custom: S. If BK: C.
2) R59 rush yes & qty>7000: chk press; free->home, busy->S. If CA: free->home, busy->C.
3) R23 acct new: chk credit; ok->home, stop->N. If qty<80: N; if also ship delivery: E.
4) R74 proof pending: E. If acct key & rush no: F; if also qty>11000: X.
Check result needed before send; extra checks OK.
""",
                 id="r5-so-impl-i0-claude-p3"),
    pytest.param([[23, 'account'], [92, 'stock'], [38, 'rush'], [65, 'proof']], (True, True),
                 """Home: FL=W BR=N ST=N BN=W. First matching rule wins, in this order; deepest exception replaces. No rule->home.
1 R23 acct=new: check credit; ok->home, stop->hold.
 qty<80: hold (no check)
  +ship=delivery: S
2 R92 stock=custom: P
 product BN: X
3 R38 rush=yes & qty>3000: check press; free->home, busy->P
 product ST: check press; free->home, busy->C
4 R65 proof=pending: S
 acct=key & rush=no: Q
  +qty>14000: C
Your replacement rules: same number replaces it in place.
""",
                 id="r5-so-impl-i1-claude-p1"),
    pytest.param([[23, 'account'], [92, 'stock'], [38, 'rush'], [65, 'proof']], (True, True),
                 """Home: FL=W BR=N ST=N BN=W. First matching rule wins, in this order; deepest exception replaces. No rule->home.
1 R23 acct=new: check credit; ok->home, stop->hold.
 qty<80: hold (no check)
  +ship=delivery: S
2 R92 stock=custom: P
 product BN: X
3 R38 rush=yes & qty>3000: check press; free->home, busy->P
 product ST: X (no check)
4 R65 proof=pending: S
 acct=key & rush=no: Q
  +qty>14000: C
Your replacement rules: same number replaces it in place.
""",
                 id="r5-so-impl-i1-claude-p2"),
    pytest.param([[23, 'account'], [92, 'stock'], [38, 'rush'], [65, 'proof']], (True, True),
                 """Home: FL=W BR=N ST=N BN=W. First matching rule wins, in this order; deepest exception replaces. No rule->home.
1 R23 acct=new: check credit; ok->home, stop->hold.
 qty<80: hold (no check)
  +ship=delivery: C
2 R92 stock=custom: P
 product BN: X
3 R38 rush=yes & qty>3000: check press; free->home, busy->P
 product ST: X (no check)
4 R65 proof=pending: S
 acct=key & rush=no: Q
  +qty>14000: C
Your replacement rules: same number replaces it in place.
""",
                 id="r5-so-impl-i1-claude-p3"),
    pytest.param([[65, 'stock'], [14, 'rush'], [47, 'account'], [50, 'proof']], (True, True),
                 """Home: BR=N BN=E PO=N BC=W. Rules in this order; first match wins; deepest exception wins. No match->home.
R65 stock custom->F; if BC->Q
R14 rush yes & qty>4000: check press, free->home, busy->F; if PO: busy->X
R47 account new: check credit, ok->home, stop->S; if qty<120->S (no check); +ship delivery->E
R50 proof pending->E; if key & rush no->hold; +qty>14000->X
A replacement rule with same number takes that rule's place in the order.
""",
                 id="r5-so-impl-i2-claude-p1"),
    pytest.param([[65, 'stock'], [14, 'rush'], [47, 'account'], [50, 'proof']], (True, True),
                 """Home: BR=N BN=E PO=N BC=W. Rules in this order; first match wins; deepest exception wins. No match->home.
R65 stock custom->F; if BC->Q
R14 rush yes & qty>4000: check press, free->home, busy->F; if PO: busy->X
R47 account new: check credit, ok->home, stop->S; if qty<120->S (no check); +ship delivery->C
R50 proof pending->E; if key & rush no->hold; +qty>14000->X
A replacement rule with same number takes that rule's place in the order.
""",
                 id="r5-so-impl-i2-claude-p2"),
    pytest.param([[65, 'stock'], [14, 'rush'], [47, 'account'], [50, 'proof']], (True, True),
                 """Home: BR=N BN=E PO=N BC=W. Rules in this order; first match wins; deepest exception wins. No match->home.
R65 stock custom->F; if BC->Q
R14 (replaced today) rush yes & qty>4000: check press, free->home, busy->F; if PO->C (no check)
R47 account new: check credit, ok->home, stop->S; if qty<120->S (no check); +ship delivery->C
R50 proof pending->E; if key & rush no->hold; +qty>14000->X
A replacement rule with same number takes that rule's place in the order.
""",
                 id="r5-so-impl-i2-claude-p3"),
    pytest.param([[53, 'stock'], [65, 'proof'], [59, 'account'], [44, 'rush']], (True, True),
                 """Home: MB,EN,BC=N; BK=E. First match in order; deepest exception wins. No match->home.
R53 stock custom->P; if BC->C.
R65 proof pending->W; if key&rush no->Q; if also qty>13000->N.
R59 account new->check credit: ok->home, stop->S; if qty<120->S (no check); if also ship delivery->W.
R44 rush yes&qty>3500->check press: free->home, busy->P; if BK: free->home, busy->N.
Your own replacements override same-numbered rule, keeping its place.
""",
                 id="r5-so-impl-i3-claude-p1"),
    pytest.param([[53, 'stock'], [65, 'proof'], [59, 'account'], [44, 'rush']], (True, True),
                 """Home: MB,EN,BC=N; BK=E. First match in order; deepest exception wins. No match->home.
R53 stock custom->P; if BC->C.
R65 proof pending->W; if key&rush no->Q; if also qty>13000->N.
R59 account new->check credit: ok->home, stop->S; if qty<120->S (no check); if also ship delivery->F.
R44 rush yes&qty>3500->check press: free->home, busy->P; if BK: free->home, busy->N.
Your own replacements override same-numbered rule, keeping its place.
""",
                 id="r5-so-impl-i3-claude-p2"),
    pytest.param([[53, 'stock'], [65, 'proof'], [59, 'account'], [44, 'rush']], (True, True),
                 """Home: MB,EN,BC=N; BK=E. First match in order; deepest exception wins. No match->home.
R53 stock custom->P; if BC->C.
R65 proof pending->W; if key&rush no->Q; if also qty>13000->F.
R59 account new->check credit: ok->home, stop->S; if qty<120->S (no check); if also ship delivery->F.
R44 rush yes&qty>3500->check press: free->home, busy->P; if BK: free->home, busy->N.
Your own replacements override same-numbered rule, keeping its place.
""",
                 id="r5-so-impl-i3-claude-p3"),
    pytest.param([[77, 'stock'], [59, 'rush'], [23, 'account'], [74, 'proof']], (True, True),
                 """Home: PO/CA/BK=W, MB=S. First matching rule wins; deepest exception replaces action/check. Later same-number rule replaces in place. Default home.
77 custom stock -> S; BK -> P.
59 rush=yes qty>7000 -> check press: free home, busy S; CA busy C (same check/free).
23 account=new -> check credit: ok home, stop N; qty<80 -> N without check, within that ship=delivery -> E.
74 proof=pending -> E; account=key AND rush=no -> F; within that qty>11000 -> C.""",
                 id="r5-so-impl-i0-codex-p1"),
    pytest.param([[77, 'stock'], [59, 'rush'], [23, 'account'], [74, 'proof']], (True, True),
                 """Home: PO/CA/BK=W, MB=S. First matching rule wins; deepest exception replaces action/check. Later same-number rule replaces in place. Default home; unknown hold.
77 custom stock -> S; BK -> P.
59 rush=yes qty>7000 -> check press: free home, busy S; CA busy C (same check/free).
23 account=new -> check credit: ok home, stop N; qty<80 -> N without check, within that ship=delivery -> E.
74 proof=pending -> E; account=key AND rush=no -> F; within that qty>11000 -> X.""",
                 id="r5-so-impl-i0-codex-p2"),
    pytest.param([[77, 'stock'], [59, 'rush'], [23, 'account'], [74, 'proof']], (True, True),
                 """Home: PO/CA/BK=W, MB=S. First matching rule wins; deepest exception replaces action/check. Later same-number rule replaces in place. Default home; unknown hold.
77 custom stock -> S; BK -> C.
59 rush=yes qty>7000 -> check press: free home, busy S; CA busy C (same check/free).
23 account=new -> check credit: ok home, stop N; qty<80 -> N without check, within that ship=delivery -> E.
74 proof=pending -> E; account=key AND rush=no -> F; within that qty>11000 -> X.""",
                 id="r5-so-impl-i0-codex-p3"),
    pytest.param([[23, 'account'], [92, 'stock'], [38, 'rush'], [65, 'proof']], (True, True),
                 """Home FL/BN=W, BR/ST=N. First matching main in order below; deepest matching exception wins, overrides checks; same-number replacements keep position. Default home.
23 new account: credit ok home/stop hold; qty<80 hold except delivery S (no check).
92 custom stock: P except BN X.
38 rush=yes AND qty>3000: press free home/busy P (ST busy C).
65 pending proof: S except key account AND rush=no: Q, but qty>14000 C.""",
                 id="r5-so-impl-i1-codex-p1"),
    pytest.param([[23, 'account'], [92, 'stock'], [38, 'rush'], [65, 'proof']], (True, True),
                 """Home FL/BN=W, BR/ST=N. First matching main in order below; deepest matching exception wins, overrides checks; same-number replacements keep position. Default home.
23 new account: credit ok home/stop hold; qty<80 hold except delivery S (no check).
92 custom stock: P except BN X.
38 rush=yes AND qty>3000: press free home/busy P; ST X without check.
65 pending proof: S except key account AND rush=no: Q, but qty>14000 C.""",
                 id="r5-so-impl-i1-codex-p2"),
    pytest.param([[23, 'account'], [92, 'stock'], [38, 'rush'], [65, 'proof']], (True, True),
                 """Home FL/BN=W, BR/ST=N. First matching main in order below; deepest matching exception wins, overrides checks; same-number replacements keep position. Default home; unknown home hold.
23 new account: credit ok home/stop hold; qty<80 hold except delivery C (no check).
92 custom stock: P except BN X.
38 rush=yes AND qty>3000: press free home/busy P; ST X without check.
65 pending proof: S except key account AND rush=no: Q, but qty>14000 C.""",
                 id="r5-so-impl-i1-codex-p3"),
    pytest.param([[65, 'stock'], [14, 'rush'], [47, 'account'], [50, 'proof']], (True, True),
                 """Order 65,14,47,50; first matching main wins; deepest matching exception overrides action/check. Later same-number rule replaces in place. Home BR=N BN=E PO=N BC=W; default home.
65 stock custom -> F; BC -> Q.
14 rush yes & qty>4000: check press free->home busy->F; PO: free->home busy->X (press).
47 account new: check credit ok->home stop->S; qty<120 -> S no check; within qty<120 ship delivery -> E.
50 proof pending -> E; key & rush no -> hold; within that qty>14000 -> X.""",
                 id="r5-so-impl-i2-codex-p1"),
    pytest.param([[65, 'stock'], [14, 'rush'], [47, 'account'], [50, 'proof']], (True, True),
                 """Order 65,14,47,50; first matching main wins; deepest matching exception overrides action/check. Later same-number rule replaces in place. Home BR=N BN=E PO=N BC=W; default home.
65 stock custom -> F; BC -> Q.
14 rush yes & qty>4000: check press free->home busy->F; PO: free->home busy->X (press).
47 account new: check credit ok->home stop->S; qty<120 -> S no check; within qty<120 ship delivery -> C.
50 proof pending -> E; key & rush no -> hold; within that qty>14000 -> X.""",
                 id="r5-so-impl-i2-codex-p2"),
    pytest.param([[65, 'stock'], [14, 'rush'], [47, 'account'], [50, 'proof']], (True, True),
                 """Order 65,14,47,50; first matching main wins; deepest matching exception overrides action/check. Later same-number rule replaces in place. Home BR=N BN=E PO=N BC=W; default home.
65 stock custom -> F; BC -> Q.
14 rush yes & qty>4000: check press free->home busy->F; PO -> C no check.
47 account new: check credit ok->home stop->S; qty<120 -> S no check; within qty<120 ship delivery -> C.
50 proof pending -> E; key & rush no -> hold; within that qty>14000 -> X.""",
                 id="r5-so-impl-i2-codex-p3"),
    pytest.param([[53, 'stock'], [65, 'proof'], [59, 'account'], [44, 'rush']], (True, True),
                 """Rules first matching main in order below; replacements retain position. Deepest matching nested exception overrides action/checks. H(home)=BK:E; MB,EN,BC:N. Default H.
53 stock custom -> P; BC -> C.
65 proof pending -> W; if key & rush=no -> Q (if qty>13000 -> N).
59 account new -> check credit: ok H, stop S; if qty<120 -> S (if ship=delivery -> W), no check.
44 rush=yes & qty>3500 -> check press: free H, busy P (BK busy N).""",
                 id="r5-so-impl-i3-codex-p1"),
    pytest.param([[53, 'stock'], [65, 'proof'], [59, 'account'], [44, 'rush']], (True, True),
                 """Rules first matching main in order below; replacements retain position. Deepest matching nested exception overrides action/checks. H(home)=BK:E; MB,EN,BC:N. Default H; unknown H hold.
53 stock custom -> P; BC -> C.
65 proof pending -> W; if key & rush=no -> Q (if qty>13000 -> N).
59 account new -> check credit: ok H, stop S; if qty<120 -> S (if ship=delivery -> F), no check.
44 rush=yes & qty>3500 -> check press: free H, busy P (BK busy N).""",
                 id="r5-so-impl-i3-codex-p2"),
    pytest.param([[53, 'stock'], [65, 'proof'], [59, 'account'], [44, 'rush']], (True, True),
                 """Rules first matching main in order below; replacements retain position. Deepest matching nested exception overrides action/checks. H(home)=BK:E; MB,EN,BC:N. Default H; unknown H hold.
53 stock custom -> P; BC -> C.
65 proof pending -> W; if key & rush=no -> Q (if qty>13000 -> F).
59 account new -> check credit: ok H, stop S; if qty<120 -> S (if ship=delivery -> F), no check.
44 rush=yes & qty>3500 -> check press: free H, busy P (BK busy N).""",
                 id="r5-so-impl-i3-codex-p3"),
    pytest.param([[77, 'stock'], [59, 'rush'], [23, 'account'], [74, 'proof']], (True, True),
                 """Home: PO W, MB S, CA W, BK W. First match in order; deepest exception wins. None->home.
1 R77 stock custom: S; BK: P
2 R59 rush yes & qty>7000: chk press free->home busy->S; CA: free->home busy->C
3 R23 acct new: chk credit ok->home stop->N; qty<80: N (no chk); +ship delivery: E
4 R74 proof pending: E; acct key & rush no: F; +qty>11000: C
Your replacements override same-number rule, keep its place.
""",
                 id="r5-so-team-i0-claude-p1"),
    pytest.param([[77, 'stock'], [59, 'rush'], [23, 'account'], [74, 'proof']], (True, True),
                 """Home: PO W, MB S, CA W, BK W. First match in order; deepest exception wins. None->home.
1 R77 stock custom: S; BK: P
2 R59 rush yes & qty>7000: chk press free->home busy->S; CA: free->home busy->C
3 R23 acct new: chk credit ok->home stop->N; qty<80: N (no chk); +ship delivery: E
4 R74 proof pending: E; acct key & rush no: F; +qty>11000: X
Your replacements override same-number rule, keep its place.
""",
                 id="r5-so-team-i0-claude-p2"),
    pytest.param([[77, 'stock'], [59, 'rush'], [23, 'account'], [74, 'proof']], (True, True),
                 """Home: PO W, MB S, CA W, BK W. First match in order; deepest exception wins. None->home.
1 R77 stock custom: S; BK: C
2 R59 rush yes & qty>7000: chk press free->home busy->S; CA: free->home busy->C
3 R23 acct new: chk credit ok->home stop->N; qty<80: N (no chk); +ship delivery: E
4 R74 proof pending: E; acct key & rush no: F; +qty>11000: X
Your replacements override same-number rule, keep its place.
""",
                 id="r5-so-team-i0-claude-p3"),
    pytest.param([[23, 'account'], [92, 'stock'], [38, 'rush'], [65, 'proof']], (True, True),
                 """Home: FL W,BR N,ST N,BN W. First match in order; deepest exception wins. No match->home.
1.R23 acct new: chk credit ok->home,stop->hold. qty<80:hold; +ship delivery:S
2.R92 stock custom:P; BN:X
3.R38 rush yes&qty>3000: chk press free->home,busy->P. ST: chk press free->home,busy->C
4.R65 proof pending:S; acct key&rush no:Q; +qty>14000:C
Your replacements of same rule# keep its place.
""",
                 id="r5-so-team-i1-claude-p1"),
    pytest.param([[23, 'account'], [92, 'stock'], [38, 'rush'], [65, 'proof']], (True, True),
                 """Home: FL W,BR N,ST N,BN W. First match in order; deepest exception wins. No match->home.
1.R23 acct new: chk credit ok->home,stop->hold. qty<80:hold; +ship delivery:S
2.R92 stock custom:P; BN:X
3.R38 rush yes&qty>3000: chk press free->home,busy->P. ST:X (no chk)
4.R65 proof pending:S; acct key&rush no:Q; +qty>14000:C
Your replacements of same rule# keep its place.
""",
                 id="r5-so-team-i1-claude-p2"),
    pytest.param([[23, 'account'], [92, 'stock'], [38, 'rush'], [65, 'proof']], (True, True),
                 """Home: FL W,BR N,ST N,BN W. First match in order; deepest exception wins. No match->home.
1.R23 acct new: chk credit ok->home,stop->hold. qty<80:hold; +ship delivery:C
2.R92 stock custom:P; BN:X
3.R38 rush yes&qty>3000: chk press free->home,busy->P. ST:X (no chk)
4.R65 proof pending:S; acct key&rush no:Q; +qty>14000:C
Your replacements of same rule# keep its place.
""",
                 id="r5-so-team-i1-claude-p3"),
    pytest.param([[20, 'rush'], [71, 'proof'], [89, 'bind'], [83, 'stock'], [56, 'account']], (True, True),
                 """Home: CA=S BK=W BC=S EN=W. First matching rule wins, in this order; deepest exception replaces. None match->home. Your replacements take the replaced rule's slot.
R20 rush=yes&qty>5500: chk press; free->home, busy->W.
 BC: chk press; free->home, busy->S.
R71 proof=pending: F.
 acct=key&rush=no: C.
  qty>12000: S.
R89 bind=perfect: X.
R83 stock=custom: W.
 EN: Q.
R56 acct=new: chk credit; ok->home, stop->hold.
 qty<130: hold.
  ship=courier: F.
""",
                 id="r5-ho-impl-i0-claude-p1"),
    pytest.param([[20, 'rush'], [71, 'proof'], [89, 'bind'], [83, 'stock'], [56, 'account']], (True, True),
                 """Home: CA=S BK=W BC=S EN=W. First matching rule wins, in this order; deepest exception replaces. None match->home. Your replacements take the replaced rule's slot.
R20 rush=yes&qty>5500: chk press; free->home, busy->W.
 BC: E (no check).
R71 proof=pending: F.
 acct=key&rush=no: C.
  qty>12000: S.
R89 bind=perfect: X.
R83 stock=custom: W.
 EN: Q.
R56 acct=new: chk credit; ok->home, stop->hold.
 qty<130: hold.
  ship=courier: F.
""",
                 id="r5-ho-impl-i0-claude-p2"),
    pytest.param([[20, 'rush'], [71, 'proof'], [89, 'bind'], [83, 'stock'], [56, 'account']], (True, True),
                 """Home: CA=S BK=W BC=S EN=W. First matching rule wins, in this order; deepest exception replaces. None match->home. Your replacements take the replaced rule's slot.
R20 rush=yes&qty>5500: chk press; free->home, busy->W.
 BC: E (no check).
R71 proof=pending: F.
 acct=key&rush=no: C.
  qty>12000: S.
R89 bind=perfect: X.
R83 stock=custom: W.
 EN: E.
R56 acct=new: chk credit; ok->home, stop->hold.
 qty<130: hold.
  ship=courier: F.
""",
                 id="r5-ho-impl-i0-claude-p3"),
    pytest.param([[20, 'rush'], [71, 'proof'], [89, 'bind'], [83, 'stock'], [56, 'account']], (True, True),
                 """Home: CA=S BK=W BC=S EN=W. First matching rule wins, in this order; deepest exception replaces. None match->home. Your replacements take the replaced rule's slot.
R20 rush=yes&qty>5500: chk press; free->home, busy->W.
 BC: E (no check).
R71 proof=pending: F.
 acct=key&rush=no: C.
  qty>12000: X.
R89 bind=perfect: X.
R83 stock=custom: W.
 EN: E.
R56 acct=new: chk credit; ok->home, stop->hold.
 qty<130: hold.
  ship=courier: F.
""",
                 id="r5-ho-impl-i0-claude-p4"),
    pytest.param([[20, 'rush'], [71, 'proof'], [89, 'bind'], [83, 'stock'], [56, 'account']], (True, True),
                 """Home: CA,BC=S; BK,EN=W. First matching main in order; deepest matching nested exception replaces action/checks. Later same-number rule replaces in place. Default home.
20 rush=yes & qty>5500: check press; free=home,busy=W; if BC busy=S (still check).
71 proof=pending:F; if account=key & rush=no:C; within that if qty>12000:S.
89 bind=perfect:X.
83 stock=custom:W; if EN:Q.
56 account=new: check credit; ok=home,stop=hold; if qty<130:hold(no check); within that if courier:F(no check).""",
                 id="r5-ho-impl-i0-codex-p1"),
    pytest.param([[20, 'rush'], [71, 'proof'], [89, 'bind'], [83, 'stock'], [56, 'account']], (True, True),
                 """Home: CA,BC=S; BK,EN=W. First matching main in order; deepest matching nested exception replaces action/checks. Later same-number rule replaces in place. Default home.
20 rush=yes & qty>5500: check press; free=home,busy=W; if BC:E(no check).
71 proof=pending:F; if account=key & rush=no:C; within that if qty>12000:S.
89 bind=perfect:X.
83 stock=custom:W; if EN:Q.
56 account=new: check credit; ok=home,stop=hold; if qty<130:hold(no check); within that if courier:F(no check).""",
                 id="r5-ho-impl-i0-codex-p2"),
    pytest.param([[20, 'rush'], [71, 'proof'], [89, 'bind'], [83, 'stock'], [56, 'account']], (True, True),
                 """Home: CA,BC=S; BK,EN=W. First matching main in order; deepest matching nested exception replaces action/checks. Later same-number rule replaces in place. Default home.
20 rush=yes & qty>5500: check press; free=home,busy=W; if BC:E(no check).
71 proof=pending:F; if account=key & rush=no:C; within that if qty>12000:S.
89 bind=perfect:X.
83 stock=custom:W; if EN:E.
56 account=new: check credit; ok=home,stop=hold; if qty<130:hold(no check); within that if courier:F(no check).""",
                 id="r5-ho-impl-i0-codex-p3"),
    pytest.param([[20, 'rush'], [71, 'proof'], [89, 'bind'], [83, 'stock'], [56, 'account']], (True, True),
                 """Home: CA,BC=S; BK,EN=W. First matching main in order; deepest matching nested exception replaces action/checks. Later same-number rule replaces in place. Default home (unknown=hold).
20 rush=yes & qty>5500: check press; free=home,busy=W; if BC:E(no check).
71 proof=pending:F; if account=key & rush=no:C; within that if qty>12000:X.
89 bind=perfect:X.
83 stock=custom:W; if EN:E.
56 account=new: check credit; ok=home,stop=hold; if qty<130:hold(no check); within that if courier:F(no check).""",
                 id="r5-ho-impl-i0-codex-p4"),
    pytest.param([[86, 'proof'], [83, 'stock'], [56, 'account'], [41, 'rush'], [77, 'bind']], (True, True),
                 """First match top-down; deepest exception wins. Same-number replacement keeps its slot.
Home: CA=E PO=S MB=W ST=W. No match->home.
86 proof pending:P
 key&rush no:F
  qty>7000:Q
83 stock custom:E
 ST:W
56 acct new:chk credit ok->home stop->hold
 qty<100:hold(no chk)
  ship courier:P
41 rush yes&qty>3000:chk press free->home busy->E
 MB:chk press free->home busy->Q
77 bind perfect:C
""",
                 id="r5-ho-impl-i1-claude-p1"),
    pytest.param([[86, 'proof'], [83, 'stock'], [56, 'account'], [41, 'rush'], [77, 'bind']], (True, True),
                 """First match top-down; deepest exception wins. Same-number replacement keeps its slot.
Home: CA=E PO=S MB=W ST=W. No match->home.
86 proof pending:P
 key&rush no:F
  qty>7000:Q
83 stock custom:E (replaced today)
 ST:F
56 acct new:chk credit ok->home stop->hold
 qty<100:hold(no chk)
  ship courier:P
41 rush yes&qty>3000:chk press free->home busy->E
 MB:chk press free->home busy->Q
77 bind perfect:C
""",
                 id="r5-ho-impl-i1-claude-p2"),
    pytest.param([[86, 'proof'], [83, 'stock'], [56, 'account'], [41, 'rush'], [77, 'bind']], (True, True),
                 """First match top-down; deepest exception wins. Same-number replacement keeps its slot.
Home: CA=E PO=S MB=W ST=W. No match->home.
86 proof pending:P
 key&rush no:F
  qty>7000:Q
83 stock custom:E
 ST:F
56 acct new:chk credit ok->home stop->hold (replaced today)
 qty<100:hold(no chk)
  ship courier:X
41 rush yes&qty>3000:chk press free->home busy->E
 MB:chk press free->home busy->Q
77 bind perfect:C
""",
                 id="r5-ho-impl-i1-claude-p3"),
    pytest.param([[86, 'proof'], [83, 'stock'], [56, 'account'], [41, 'rush'], [77, 'bind']], (True, True),
                 """First match top-down; deepest exception wins. Same-number replacement keeps its slot.
Home: CA=E PO=S MB=W ST=W. No match->home.
86 proof pending:P (replaced 11-25)
 key&rush no:F
  qty>7000:C
83 stock custom:E
 ST:F
56 acct new:chk credit ok->home stop->hold
 qty<100:hold(no chk)
  ship courier:X
41 rush yes&qty>3000:chk press free->home busy->E
 MB:chk press free->home busy->Q
77 bind perfect:C
""",
                 id="r5-ho-impl-i1-claude-p4"),
    pytest.param([[86, 'proof'], [83, 'stock'], [56, 'account'], [41, 'rush'], [77, 'bind']], (True, True),
                 """First matching rule in order; same-number replacement stays in slot. Nested exceptions inherit conditions, deepest replaces action/checks. Home H: CA E, PO S, MB/ST W. Default H.
86 proof pending: P; key & rush no: F, within that qty>7000: Q.
83 custom stock: E; ST: W.
56 new account: check credit ok H/stop hold; qty<100: hold, within that courier: P (no check).
41 rush yes & qty>3000: check press free H/busy E; MB: same check free H/busy Q.
77 perfect bind: C.""",
                 id="r5-ho-impl-i1-codex-p1"),
    pytest.param([[86, 'proof'], [83, 'stock'], [56, 'account'], [41, 'rush'], [77, 'bind']], (True, True),
                 """First matching rule in order; same-number replacement stays in slot. Nested exceptions inherit conditions, deepest replaces action/checks. Home H: CA E, PO S, MB/ST W. Default H; unknown hold.
86 proof pending: P; key & rush no: F, within that qty>7000: Q.
83 custom stock: E; ST: F.
56 new account: check credit ok H/stop hold; qty<100: hold, within that courier: P (no check).
41 rush yes & qty>3000: check press free H/busy E; MB: same check free H/busy Q.
77 perfect bind: C.""",
                 id="r5-ho-impl-i1-codex-p2"),
    pytest.param([[86, 'proof'], [83, 'stock'], [56, 'account'], [41, 'rush'], [77, 'bind']], (True, True),
                 """First matching rule in order; same-number replacement stays in slot. Nested exceptions inherit conditions, deepest replaces action/checks. Home H: CA E, PO S, MB/ST W. Default H; unknown hold.
86 proof pending: P; key & rush no: F, within that qty>7000: Q.
83 custom stock: E; ST: F.
56 new account: check credit ok H/stop hold; qty<100: hold, within that courier: X (no check).
41 rush yes & qty>3000: check press free H/busy E; MB: same check free H/busy Q.
77 perfect bind: C.""",
                 id="r5-ho-impl-i1-codex-p3"),
    pytest.param([[86, 'proof'], [83, 'stock'], [56, 'account'], [41, 'rush'], [77, 'bind']], (True, True),
                 """First matching rule in order; same-number replacement stays in slot. Nested exceptions inherit conditions, deepest replaces action/checks. Home H: CA E, PO S, MB/ST W. Default H; unknown hold.
86 proof pending: P; key & rush no: F, within that qty>7000: C.
83 custom stock: E; ST: F.
56 new account: check credit ok H/stop hold; qty<100: hold, within that courier: X (no check).
41 rush yes & qty>3000: check press free H/busy E; MB: same check free H/busy Q.
77 perfect bind: C.""",
                 id="r5-ho-impl-i1-codex-p4"),
    pytest.param([[29, 'account'], [38, 'stock'], [23, 'rush'], [62, 'proof']], (True, True),
                 """Home: BN=S EN=W ST=E BC=N. First matching rule in order; deepest exception wins. No match: home.
1) R29 account=new: chk credit; ok->home, stop->W.
 qty<60: W. +ship=courier: hold.
2) R38 stock=custom: X. product BC: C.
3) R23 rush=yes & qty>6500: chk press; free->home, busy->X. ST: chk press; free->home, busy->P.
4) R62 proof=pending: hold. account=key & rush=no: N. +qty>9000: P.
Your replacements with same rule no. take that rule's place.
""",
                 id="r5-so-impl-i4-claude-p1"),
    pytest.param([[29, 'account'], [38, 'stock'], [23, 'rush'], [62, 'proof']], (True, True),
                 """Home: BN=S EN=W ST=E BC=N. First matching rule in order; deepest exception wins. No match: home.
1) R29 account=new: chk credit; ok->home, stop->W.
 qty<60: W. +ship=courier: hold.
2) R38 stock=custom: X. product BC: C.
3) R23 rush=yes & qty>6500: chk press; free->home, busy->X. ST: C (no chk).
4) R62 proof=pending: hold. account=key & rush=no: N. +qty>9000: P.
Your replacements with same rule no. take that rule's place.
""",
                 id="r5-so-impl-i4-claude-p2"),
    pytest.param([[29, 'account'], [38, 'stock'], [23, 'rush'], [62, 'proof']], (True, True),
                 """Home: BN=S EN=W ST=E BC=N. First matching rule in order; deepest exception wins. No match: home.
1) R29 account=new: chk credit; ok->home, stop->W.
 qty<60: W. +ship=courier: hold.
2) R38 stock=custom: X. product BC: Q.
3) R23 rush=yes & qty>6500: chk press; free->home, busy->X. ST: C (no chk).
4) R62 proof=pending: hold. account=key & rush=no: N. +qty>9000: P.
Your replacements with same rule no. take that rule's place.
""",
                 id="r5-so-impl-i4-claude-p3"),
    pytest.param([[74, 'rush'], [26, 'proof'], [41, 'account'], [86, 'stock']], (True, True),
                 """Home: BK=E PO=S MB=N FL=E. First match in order; deepest exception wins. None->home.
R74 rush=yes&qty>4500: chk press; free->home, busy->F
 ex MB: chk press; free->home, busy->W
R26 proof=pending: N
 ex account=key&rush=no: HOLD
  ex qty>12000: W
R41 account=new: chk credit; ok->home, stop->X
 ex qty<90: X
  ex ship=delivery: N
R86 stock=custom: F
 ex FL: S
Your replacements (same rule no.) replace in place.
""",
                 id="r5-so-impl-i5-claude-p1"),
    pytest.param([[74, 'rush'], [26, 'proof'], [41, 'account'], [86, 'stock']], (True, True),
                 """Home: BK=E PO=S MB=N FL=E. First match in order; deepest exception wins. None->home.
R74 rush=yes&qty>4500: chk press; free->home, busy->F
 ex MB: P (no check)
R26 proof=pending: N
 ex account=key&rush=no: HOLD
  ex qty>12000: W
R41 account=new: chk credit; ok->home, stop->X
 ex qty<90: X
  ex ship=delivery: N
R86 stock=custom: F
 ex FL: S
Your replacements (same rule no.) replace in place.
""",
                 id="r5-so-impl-i5-claude-p2"),
    pytest.param([[74, 'rush'], [26, 'proof'], [41, 'account'], [86, 'stock']], (True, True),
                 """Home: BK=E PO=S MB=N FL=E. First match in order; deepest exception wins. None->home.
R74 rush=yes&qty>4500: chk press; free->home, busy->F
 ex MB: P (no check)
R26 proof=pending: N
 ex account=key&rush=no: HOLD
  ex qty>12000: W
R41 account=new: chk credit; ok->home, stop->X
 ex qty<90: X
  ex ship=delivery: C
R86 stock=custom: F
 ex FL: S
Your replacements (same rule no.) replace in place.
""",
                 id="r5-so-impl-i5-claude-p3"),
    pytest.param([[65, 'proof'], [62, 'rush'], [17, 'account'], [50, 'stock']], (True, True),
                 """Home: EN=W FL=S CA=E BR=W. First matching rule in order; deepest exception wins. No match->home.
R65 proof pending->X
 if key & rush no->P
  if also qty>12000->N
R62 rush yes & qty>3000: check press; free->home, busy->F
 if CA: check press; free->home, busy->N
R17 account new: check credit; ok->home, stop->E
 if qty<120->E (no check)
  if also ship courier->X
R50 stock custom->F
 if BR->S
Your own replacements (same rule no.) replace these in place.
""",
                 id="r5-so-impl-i6-claude-p1"),
    pytest.param([[65, 'proof'], [62, 'rush'], [17, 'account'], [50, 'stock']], (True, True),
                 """Home: EN=W FL=S CA=E BR=W. First matching rule in order; deepest exception wins. No match->home.
R65 proof pending->X
 if key & rush no->P
  if also qty>12000->N
R62 rush yes & qty>3000: check press; free->home, busy->F
 if CA: check press; free->home, busy->N
R17 account new: check credit; ok->home, stop->E
 if qty<120->E (no check)
  if also ship courier->C
R50 stock custom->F
 if BR->S
Your own replacements (same rule no.) replace these in place.
""",
                 id="r5-so-impl-i6-claude-p2"),
    pytest.param([[65, 'proof'], [62, 'rush'], [17, 'account'], [50, 'stock']], (True, True),
                 """Home: EN=W FL=S CA=E BR=W. First matching rule in order; deepest exception wins. No match->home.
R65 proof pending->X
 if key & rush no->P
  if also qty>12000->C
R62 rush yes & qty>3000: check press; free->home, busy->F
 if CA: check press; free->home, busy->N
R17 account new: check credit; ok->home, stop->E
 if qty<120->E (no check)
  if also ship courier->C
R50 stock custom->F
 if BR->S
Your own replacements (same rule no.) replace these in place.
""",
                 id="r5-so-impl-i6-claude-p3"),
    pytest.param([[62, 'account'], [77, 'stock'], [35, 'rush'], [92, 'proof']], (True, True),
                 """First match in order; deepest exception wins. No match: home.
Home: MB=S BN=W FL=E EN=W
R62 account=new: check credit; ok->home, stop->F.
 qty<50: F. +ship=delivery: Q.
R77 stock=custom: P. product EN: hold.
R35 rush=yes & qty>7500: check press; free->home, busy->P.
 product FL: check press; free->home, busy->W.
R92 proof=pending: Q.
 account=key & rush=no: E. +qty>9000: W.
Your replacements: same number replaces rule in its place.
""",
                 id="r5-so-impl-i7-claude-p1"),
    pytest.param([[62, 'account'], [77, 'stock'], [35, 'rush'], [92, 'proof']], (True, True),
                 """First match in order; deepest exception wins. No match: home.
Home: MB=S BN=W FL=E EN=W
R62 account=new: check credit; ok->home, stop->F.
 qty<50: F. +ship=delivery: N.
R77 stock=custom: P. product EN: hold.
R35 rush=yes & qty>7500: check press; free->home, busy->P.
 product FL: check press; free->home, busy->W.
R92 proof=pending: Q.
 account=key & rush=no: E. +qty>9000: W.
Your replacements: same number replaces rule in its place.
""",
                 id="r5-so-impl-i7-claude-p2"),
    pytest.param([[62, 'account'], [77, 'stock'], [35, 'rush'], [92, 'proof']], (True, True),
                 """First match in order; deepest exception wins. No match: home.
Home: MB=S BN=W FL=E EN=W
R62 account=new: check credit; ok->home, stop->F.
 qty<50: F. +ship=delivery: N.
R77 stock=custom: P. product EN: hold.
R35 rush=yes & qty>7500: check press; free->home, busy->P.
 product FL: X (no check).
R92 proof=pending: Q.
 account=key & rush=no: E. +qty>9000: W.
Your replacements: same number replaces rule in its place.
""",
                 id="r5-so-impl-i7-claude-p3"),
]


@pytest.mark.parametrize("heads,expected,note", NOTES)
def test_playtest_note(heads, expected, note):
    measured = EXT.note_measures({"card": False, "rule_heads": heads}, note)
    assert (measured["order_kept"], measured["numbers_kept"]) == expected
    assert measured["note_measure_flag"] is None
