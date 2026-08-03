"""
Builds questions.json for the prep-mode backtest from the 16 readable VNIT OS
papers (2017-2024). Concepts hand-tagged by reading each question against the
12-concept gloss in prep_mode.py. This stands in for the LLM tagger so the
scoring/backtest pipeline runs fully offline and reproducibly.

Each entry: (q_id, year, marks, concept_list). `marks` = per-question marks
where the paper labelled them, else the section total, else None.

EDIT FREELY: if you disagree with a tag, change it here and rerun. That is the
whole point of doing this as a readable builder instead of a black box.
"""
import json

# (q_id, year, marks, [concepts], short_label)
Q = [
    # ===================== 2017 =====================
    # --- CT1-OS-W17 (First Sessional, 15) ---
    ("2017-ct1-1", 2017, 6, ["cpu-virtualization"], "write() syscall, library vs syscall, flow of control"),
    ("2017-ct1-2", 2017, 6, ["file-systems"], "hard vs soft links properties"),
    ("2017-ct1-3", 2017, 3, ["file-systems", "data-integrity"], "idempotent free-list op, backups, floppy corruption"),
    # --- CT2-OS-W17 (Second Sessional, 15) ---
    ("2017-ct2-1", 2017, 3, ["persistence"], "I/O software layers, buffering, packet transmit"),
    ("2017-ct2-2", 2017, 3, ["data-integrity", "memory-virtualization", "persistence"], "stable storage, segmented paging, disk arm scheduling T/F"),
    ("2017-ct2-3", 2017, 9, ["paging", "memory-virtualization"], "page table size 20-bit VA, buddy allocation"),
    # --- EndSem-OS-W17 (50) ---
    ("2017-end-1a", 2017, 10, ["memory-virtualization"], "swapping, base-limit MMU"),
    ("2017-end-1b", 2017, 10, ["memory-virtualization"], "internal vs external fragmentation"),
    ("2017-end-1c", 2017, 10, ["computer-architecture"], "cache hit ratio / effective access time"),
    ("2017-end-1d", 2017, 10, ["paging"], "multi-level page table 34-bit address"),
    ("2017-end-2a", 2017, 10, ["file-systems"], "inode reference count field"),
    ("2017-end-2b", 2017, 10, ["file-systems"], "direct blocks in inode"),
    ("2017-end-2c", 2017, 10, ["file-systems"], "max file size single/double/triple indirection"),
    ("2017-end-2d", 2017, 10, ["file-systems"], "why not only triple indirection"),
    ("2017-end-2e", 2017, 10, ["data-integrity"], "fsck consistency checks"),
    ("2017-end-3a", 2017, 10, ["paging"], "page replacement OPT/LRU/FIFO faults"),
    ("2017-end-3b", 2017, 10, ["locks"], "Banker's algorithm, deadlock, safe sequence"),
    ("2017-end-4a", 2017, 20, ["threads"], "user-level vs kernel-level threads"),
    ("2017-end-4b", 2017, 20, ["threads", "concurrency"], "producer/consumer semaphores correctness"),
    ("2017-end-4c", 2017, 20, ["concurrency", "locks"], "thread interleaving x/y with tset locks"),
    # --- ReExam-OS-W17 (50) ---
    ("2017-re-1", 2017, 5, ["persistence"], "floppy seek / block clustering read time"),
    ("2017-re-2", 2017, 5, ["file-systems"], "unix path resolution disk accesses"),
    ("2017-re-3", 2017, 5, ["concurrency"], "thread interleaving c=b±a possible values"),
    ("2017-re-4", 2017, 5, ["paging"], "22-bit VA VPN/offset, page table entries"),
    ("2017-re-5", 2017, 5, ["paging"], "TLB behavior p[1024] hits/misses"),
    ("2017-re-6", 2017, 5, ["paging"], "associative registers serial vs parallel lookup"),
    ("2017-re-7", 2017, 5, ["threads"], "counting semaphore from binary semaphores"),
    ("2017-re-8", 2017, 5, ["locks"], "test-and-set from swap instruction"),
    ("2017-re-9", 2017, 5, ["scheduling"], "FCFS/SJF/priority/RR gantt, turnaround/waiting"),
    ("2017-re-10", 2017, 5, ["persistence"], "RAID 10 advantages and stripe placement"),

    # ===================== 2018 =====================
    # --- CT1-OS-W18 (First Sessional, 15) ---
    ("2018-ct1-1", 2018, 6, ["cpu-virtualization"], "write() count return, syscall naming, flow of control"),
    ("2018-ct1-2", 2018, 6, ["file-systems"], "hard/soft links H/S/B/N classification"),
    ("2018-ct1-3", 2018, 3, ["file-systems", "data-integrity"], "idempotent free-list, backups, floppy corruption"),
    # --- CT2-OS-W18 (Second Sessional, 15) ---
    ("2018-ct2-1", 2018, 6, ["persistence"], "disk arm scheduling FIFO/SSTF/SCAN/LOOK/C-SCAN/C-LOOK"),
    ("2018-ct2-2", 2018, 6, ["paging"], "logical address space, frames, page tables, fragmentation"),
    ("2018-ct2-3", 2018, 3, ["paging", "memory-virtualization"], "swap fast, sparse address space, min frames indirection"),
    # --- EndSem-OS-W18 (50) ---
    ("2018-end-1a", 2018, 15, ["paging"], "TLB/page-table access time, hit rates"),
    ("2018-end-1b", 2018, 15, ["memory-virtualization"], "Buddy allocator pseudocode + trace"),
    ("2018-end-1c", 2018, 15, ["paging"], "page replacement LRU/FIFO/OPT across frame counts"),
    ("2018-end-2a", 2018, 10, ["file-systems"], "directory implementations"),
    ("2018-end-2b", 2018, 10, ["file-systems"], "partition blocks, inode blocks count"),
    ("2018-end-2c", 2018, 10, ["data-integrity"], "fsck checks, in-use vs free block lists"),
    ("2018-end-3a", 2018, 10, ["locks"], "Banker's algorithm safe state"),
    ("2018-end-3b", 2018, 10, ["locks"], "deadlock prevention, circular wait"),
    ("2018-end-4a", 2018, 15, ["processes"], "fork/wait valid outputs interleaving"),
    ("2018-end-4b", 2018, 15, ["scheduling"], "priority scheduling Gantt, turnaround/response"),
    ("2018-end-4c", 2018, 15, ["threads"], "3-process iteration barrier with semaphores"),
    ("2018-end-4d", 2018, 15, ["threads"], "three typical uses of semaphores"),

    # ===================== 2019 =====================
    # --- First_Sessional_27_aug (15) ---
    ("2019-fs-1a", 2019, 1.5, ["cpu-virtualization"], "user->kernel transition steps"),
    ("2019-fs-1b", 2019, 1.0, ["processes"], "fork count new processes"),
    ("2019-fs-1c", 2019, 1.0, ["processes", "file-systems"], "fork+open outfile contents (shared offset)"),
    ("2019-fs-1d", 2019, 1.0, ["processes", "file-systems"], "fork before open, separate offsets"),
    ("2019-fs-2", 2019, 3, ["scheduling"], "Round Robin strengths/weaknesses, quanta"),
    ("2019-fs-3", 2019, 3.5, ["scheduling"], "MLFQ queues, time slices, priority boost"),
    ("2019-fs-4", 2019, 4, ["memory-virtualization"], "segmentation VA trace valid/violation"),
    # --- EndSem-OS-W19 (50) ---
    ("2019-end-1a", 2019, 1.5, ["computer-architecture"], "hardware aids for OS functionality"),
    ("2019-end-1b", 2019, 1.5, ["cpu-virtualization"], "limited direct execution benefit"),
    ("2019-end-1c", 2019, 1.5, ["threads"], "benefits of multi-threaded programs"),
    ("2019-end-1d", 2019, 1.5, ["file-systems"], "disk accesses to create nested file"),
    ("2019-end-1e", 2019, 1.5, ["locks"], "CPU utilization if all threads deadlocked"),
    ("2019-end-1f", 2019, 2.5, ["paging", "persistence", "threads"], "MCQ: multilevel PT, seek latency, TLB pipeline, IPC, semaphore order"),
    ("2019-end-2a", 2019, 4, ["scheduling"], "two-queue scheduler priority/starvation/fairness"),
    ("2019-end-2b", 2019, 5, ["memory-virtualization", "paging"], "segmentation logical address, multi-level split"),
    ("2019-end-2c", 2019, 4, ["paging"], "TLB misses STRIDE/MAX tuning"),
    ("2019-end-3a", 2019, 4, ["file-systems"], "inode direct/indirect max disk & file size"),
    ("2019-end-3b", 2019, 4, ["file-systems"], "per-process vs system open file table"),
    ("2019-end-3c", 2019, 4, ["persistence"], "disk service time RPM/seek, SSTF/SPTF order"),
    ("2019-end-4a", 2019, 3, ["locks"], "CompareAndSwap lock-free linked list insert"),
    ("2019-end-4b", 2019, 3, ["threads"], "condition variable from semaphore bug"),
    ("2019-end-4c", 2019, 3, ["locks"], "deadlock possible across P1/P2/P3 requests"),
    ("2019-end-4d", 2019, 6, ["threads"], "bridge crossing sync with semaphores"),

    # ===================== 2020 (End_sem_solutions, 30) =====================
    ("2020-end-1", 2020, 4.5, ["processes", "file-systems"], "fork/dup2/execvp grep, fd tables"),
    ("2020-end-2", 2020, 3, ["persistence"], "disk seek/clustering 50-block read time"),
    ("2020-end-3", 2020, 5, ["threads"], "three-process P/Q/R synchronization"),
    ("2020-end-4", 2020, 5, ["concurrency"], "pseudo-code interleaving / atomicity"),
    ("2020-end-5", 2020, 5, ["locks"], "deadlock occurrence example scenario"),
    ("2020-end-6", 2020, 5, ["file-systems"], "Unix file system API design advantages"),

    # ===================== 2021 (CS309 End_sem, 35) =====================
    ("2021-end-1", 2021, 4, ["threads"], "semaphore ordering A;B;C;D repeating"),
    ("2021-end-2", 2021, 5, ["threads"], "counting semaphore from binary + counter"),
    ("2021-end-3", 2021, 8, ["locks"], "Banker's algorithm safe state + request grant"),
    ("2021-end-4", 2021, 4, ["file-systems"], "inode ref count, IO completion, triple indirection"),
    ("2021-end-5", 2021, 8, ["file-systems"], "open/write/dup file structure changes"),
    ("2021-end-6", 2021, 6, ["persistence"], "disk SATF/FIFO rotational worst-case"),

    # ===================== 2022 =====================
    # --- Second_Sessional_students (15) ---
    ("2022-ss-1", 2022, 2, ["memory-virtualization"], "two-segment translation valid/fault"),
    ("2022-ss-2", 2022, 2, ["paging"], "TLB misses STRIDE/MAX"),
    ("2022-ss-3", 2022, 2, ["paging"], "software-managed TLB miss problem"),
    ("2022-ss-4", 2022, 2, ["threads"], "thread vs process creation interface"),
    ("2022-ss-5", 2022, 3, ["locks"], "spin lock xchg alternative correctness"),
    ("2022-ss-6", 2022, 4, ["paging"], "two-level page table PDE/PTE translation"),
    # --- Reexam_W22 (50) ---
    ("2022-re-1", 2022, 6, ["cpu-virtualization", "processes"], "policy vs mechanism, process states, stack/heap"),
    ("2022-re-2", 2022, 8, ["memory-virtualization"], "dynamic relocation base/bounds registers"),
    ("2022-re-3", 2022, 6, ["concurrency", "threads"], "many-threads C snippet race"),
    ("2022-re-8", 2022, 8, ["threads"], "one-lane bridge traffic synchronization"),

    # ===================== 2024 =====================
    # --- CSL_309_Mid_sem_Paper_students (25) ---
    ("2024-mid-1", 2024, 2, ["cpu-virtualization"], "syscall kernel address, userspace context save"),
    ("2024-mid-2", 2024, 2.5, ["processes"], "fork loop output justification"),
    ("2024-mid-3", 2024, 2.5, ["scheduling"], "which policies allow given A/B/C schedule (MLFQ/lottery)"),
    ("2024-mid-4", 2024, 4, ["scheduling"], "Response-Ratio scheduling Gantt, avg response/turnaround"),
    ("2024-mid-5", 2024, 4, ["threads", "concurrency"], "concurrent linked list insert/lookup with mutex"),
    ("2024-mid-6", 2024, 3, ["concurrency"], "thread interleaving c=b±a other values"),
    ("2024-mid-7", 2024, 3, ["threads"], "condition variable wait() mutex + while predicate"),
    ("2024-mid-8", 2024, 4, ["threads"], "K+1 threads N*T1 then T2 cyclic CV sync"),
    # --- OS-EExam-W24 (6 Q) ---
    ("2024-ee-1", 2024, None, ["cpu-virtualization"], "two CPU modes, importance in virtualization"),
    ("2024-ee-2", 2024, None, ["scheduling"], "FCFS vs SRTF avg turnaround difference"),
    ("2024-ee-3", 2024, None, ["paging"], "TLB valid bit vs page-table valid bit"),
    ("2024-ee-4", 2024, None, ["paging"], "multi-level page table hypothetical calcs"),
    ("2024-ee-5", 2024, None, ["paging"], "page fault service time avg memory access"),
    ("2024-ee-6", 2024, None, ["memory-virtualization", "paging"], "segmented paging min page size; LRU replacement"),
]

# exam_type derived from the q_id prefix: ct1/ct2/-fs-/-ss-/-mid- => midsem,
# -end-/-ee- => endsem, -re- => reexam.
#
# UPDATE: seasonal/re-exams don't happen anymore, so they're no longer a real
# exam students will sit -- they were previously being silently folded into
# "endsem" (any q_id without a midsem marker fell through to endsem), which
# quietly polluted the endsem analysis with a paper type that isn't
# predictive of anything current. reexam is now its own exam_type so it's
# excluded from prep-mode scoring/backtesting by construction (tune_decay.py
# only ever looks at "midsem" and "endsem"). Reexam questions are NOT thrown
# away -- ANALYSIS_EXAM_TYPES below is the filter to use when building the
# prep-mode dataset; everything outside it (reexam, and non-exam folders like
# seasonal/quizzes/practice questions/question banks) should instead be
# ingested as teaching material into Prof Oak's RAG index, not scored.
MIDSEM_MARKERS = ("ct1", "ct2", "-fs-", "-ss-", "-mid-")
REEXAM_MARKERS = ("-re-",)
ANALYSIS_EXAM_TYPES = ("midsem", "endsem")  # what prep-mode analysis trains/tests on


def _exam_type(q_id: str) -> str:
    if any(m in q_id for m in MIDSEM_MARKERS):
        return "midsem"
    if any(m in q_id for m in REEXAM_MARKERS):
        return "reexam"
    return "endsem"


rows = [{"q_id": q, "year": y, "marks": m, "concepts": c, "text": lbl,
         "exam_type": _exam_type(q)}
        for (q, y, m, c, lbl) in Q]

with open("data/questions.json", "w") as f:
    json.dump(rows, f, indent=2)

# analysis-eligible subset: this is what tune_decay.py / prep_mode.py should
# actually load for scoring. reexam rows are written separately so nothing
# gets silently dropped -- they route to the teaching-material pipeline.
analysis_rows = [r for r in rows if r["exam_type"] in ANALYSIS_EXAM_TYPES]
teaching_rows = [r for r in rows if r["exam_type"] not in ANALYSIS_EXAM_TYPES]

with open("data/questions_analysis.json", "w") as f:
    json.dump(analysis_rows, f, indent=2)
with open("data/questions_teaching_material.json", "w") as f:
    json.dump(teaching_rows, f, indent=2)

# quick sanity summary
from collections import Counter
by_year = Counter(r["year"] for r in rows)
by_concept = Counter(c for r in rows for c in r["concepts"])
no_marks = sum(1 for r in rows if r["marks"] is None)
print(f"wrote {len(rows)} questions to data/questions.json")
print(f"  -> {len(analysis_rows)} analysis-eligible (midsem+endsem) to data/questions_analysis.json")
print(f"  -> {len(teaching_rows)} routed to teaching material (data/questions_teaching_material.json)")
print("by year:", dict(sorted(by_year.items())))
print("distinct years:", len(by_year))
print("null-marks questions:", no_marks)
by_exam = Counter(r["exam_type"] for r in rows)
print("by exam_type:", dict(by_exam))
print("\nconcept coverage (times tagged, all rows incl. reexam):")
for c, n in by_concept.most_common():
    print(f"  {c:<24}{n}")