"""
seed_bots.py — MAROS analytics seeder
Creates 130 fake students in student_profiles + realistic quiz_answers
+ interaction_log events, directly in Supabase. No auth, no LLM calls.

Run:    python seed_bots.py
Clean:  python seed_bots.py --clean     (deletes all bot data)
"""

import os
import sys
import uuid
import random
from datetime import datetime, timedelta

from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))

random.seed(42)   # reproducible — same class every run

# ── OS concepts (mirrors your 12-concept seed graph) ─────────────────
CONCEPTS = [
    "Process Management", "CPU Scheduling", "Threads and Concurrency",
    "Synchronization", "Deadlocks", "Memory Management",
    "Virtual Memory", "Paging and Segmentation", "File Systems",
    "Disk Scheduling", "I/O Systems", "System Calls",
]

# Per-concept difficulty: probability a MEDIAN student gets it wrong
CONCEPT_DIFFICULTY = {
    "Virtual Memory": 0.55, "Deadlocks": 0.50, "Synchronization": 0.48,
    "Paging and Segmentation": 0.45, "CPU Scheduling": 0.35,
    "Threads and Concurrency": 0.35, "Memory Management": 0.30,
    "Disk Scheduling": 0.30, "Process Management": 0.22,
    "File Systems": 0.25, "I/O Systems": 0.28, "System Calls": 0.18,
}

MISCONCEPTIONS = {
    "Virtual Memory": [
        "Believes virtual memory is a physical portion of the hard disk rather than an abstraction layer",
        "Thinks page faults always indicate a program error rather than normal demand paging",
        "Confuses virtual address space size with installed RAM size",
    ],
    "Deadlocks": [
        "Believes deadlock can occur with only one of the four Coffman conditions present",
        "Confuses deadlock prevention with deadlock avoidance (Banker's algorithm)",
        "Thinks a cycle in the resource allocation graph always means deadlock even with multiple instances",
    ],
    "Synchronization": [
        "Believes a mutex and a binary semaphore are identical, ignoring ownership semantics",
        "Thinks disabling interrupts is a valid synchronization method on multiprocessor systems",
        "Confuses race conditions with deadlocks",
    ],
    "CPU Scheduling": [
        "Believes SJF is always optimal in practice, ignoring that burst times are unknown ahead of time",
        "Confuses turnaround time with waiting time",
        "Thinks Round Robin with a very small quantum improves performance, ignoring context-switch overhead",
    ],
    "Paging and Segmentation": [
        "Believes paging eliminates all fragmentation, forgetting internal fragmentation",
        "Confuses the page table with the TLB",
    ],
    "Memory Management": [
        "Thinks first-fit always wastes more memory than best-fit",
        "Confuses logical and physical addresses",
    ],
}
GENERIC_MISC = "Confuses {c} terminology and applies the wrong mechanism to the scenario"

FIRST = ["Aarav","Vivaan","Aditya","Arjun","Sai","Reyansh","Krishna","Ishaan","Rohan","Kunal",
         "Ananya","Diya","Aadhya","Saanvi","Pari","Anika","Navya","Riya","Ishita","Sneha",
         "Rahul","Amit","Nikhil","Siddharth","Varun","Karan","Harsh","Yash","Dev","Pranav",
         "Pooja","Neha","Shruti","Kavya","Tanvi","Meera","Priya","Divya","Nidhi","Aisha"]
LAST  = ["Sharma","Verma","Patil","Deshmukh","Kulkarni","Joshi","Singh","Gupta","Mehta","Iyer",
         "Reddy","Nair","Kale","Bhosale","Chavan","Pawar","Shinde","Jadhav","Agarwal","Rao"]

# Module IDs shaped exactly like your real ones: {job_id}_modNN
FAKE_JOB = "seedjob01"

Q_TEMPLATES = [
    "In the context of {c}, which of the following statements is correct?",
    "A process exhibits the following behavior related to {c}. What is the most likely cause?",
    "Which algorithm/mechanism best addresses this {c} scenario?",
    "What happens to system performance when {c} is misconfigured as described?",
]

def make_students(n=130):
    students = []
    for i in range(n):
        name = f"{random.choice(FIRST)} {random.choice(LAST)}"
        # Ability: N(0.62, 0.18) clamped — realistic spread with real strugglers
        ability = max(0.15, min(0.95, random.gauss(0.62, 0.18)))
        students.append({
            "id":      str(uuid.uuid4()),
            "name":    name,
            "roll_no": f"BT23CSE{i+1:03d}",
            "ability": ability,
        })
    return students

def seed():
    students = make_students(130)

    print("Inserting 130 profiles...")
    sb.table("student_profiles").upsert([
        {"id": s["id"], "name": s["name"], "roll_no": s["roll_no"], "is_bot": True}
        for s in students
    ]).execute()

    answers, events = [], []
    now = datetime.utcnow()

    for s in students:
        # Each student takes quizzes on 4-9 concepts, 5 questions each
        taken = random.sample(CONCEPTS, k=random.randint(4, 9))
        for ci, concept in enumerate(taken):
            module_id = f"{FAKE_JOB}_mod{CONCEPTS.index(concept)+1:02d}"
            quiz_time = now - timedelta(days=random.randint(0, 14),
                                        hours=random.randint(0, 12))
            n_correct = 0
            for qn in range(5):
                difficulty = CONCEPT_DIFFICULTY[concept]
                # P(wrong) = difficulty scaled by inverse ability
                p_wrong    = min(0.92, max(0.05, difficulty * (1.35 - s["ability"])))
                is_correct = random.random() > p_wrong
                if is_correct:
                    n_correct += 1

                correct_key = random.choice("ABCD")
                chosen_key  = correct_key if is_correct else random.choice(
                    [k for k in "ABCD" if k != correct_key])

                misc = None
                conf = None
                if not is_correct:
                    pool = MISCONCEPTIONS.get(concept)
                    misc = random.choice(pool) if pool else GENERIC_MISC.format(c=concept)
                    conf = round(random.uniform(0.6, 0.95), 2)

                answers.append({
                    "student_id":           s["id"],
                    "module_id":            module_id,
                    "question_text":        random.choice(Q_TEMPLATES).format(c=concept),
                    "options":              {"A": "Option A", "B": "Option B",
                                             "C": "Option C", "D": "Option D"},
                    "chosen_answer":        chosen_key,
                    "correct_answer":       correct_key,
                    "is_correct":           is_correct,
                    "concept_id":           concept,
                    "root_concept_id":      concept,
                    "misconception":        misc,
                    "diagnosis_confidence": conf,
                    "answered_at":          (quiz_time + timedelta(seconds=qn*45)).isoformat(),
                })

            events.append({
                "student_id":       s["id"],
                "event_type":       "quiz_complete",
                "module_id":        module_id,
                "concept_id":       concept,
                "payload":          {"total": 5, "correct": n_correct,
                                     "score": n_correct / 5},
                "response_time_ms": random.randint(60_000, 300_000),
                "ts":               (quiz_time + timedelta(minutes=5)).isoformat(),
            })
            # Weak students emit struggle signals
            if s["ability"] < 0.45 and random.random() < 0.5:
                events.append({
                    "student_id": s["id"],
                    "event_type": "struggle_signal",
                    "concept_id": concept,
                    "payload":    {"message": f"i dont understand {concept.lower()}"},
                    "ts":         (quiz_time + timedelta(minutes=8)).isoformat(),
                })

    print(f"Inserting {len(answers)} quiz answers (batches of 500)...")
    for i in range(0, len(answers), 500):
        sb.table("quiz_answers").insert(answers[i:i+500]).execute()

    print(f"Inserting {len(events)} interaction events...")
    for i in range(0, len(events), 500):
        sb.table("interaction_log").insert(events[i:i+500]).execute()

    print(f"\nDone. 130 students, {len(answers)} answers, {len(events)} events.")
    print("Open Analytics tab -> Generate Report.")

def clean():
    print("Deleting bot data...")
    bots = sb.table("student_profiles").select("id").eq("is_bot", True).execute().data or []
    ids  = [b["id"] for b in bots]
    for i in range(0, len(ids), 50):
        chunk = ids[i:i+50]
        sb.table("quiz_answers").delete().in_("student_id", chunk).execute()
        sb.table("interaction_log").delete().in_("student_id", chunk).execute()
        sb.table("student_profiles").delete().in_("id", chunk).execute()
    print(f"Removed {len(ids)} bots and all their data.")

if __name__ == "__main__":
    clean() if "--clean" in sys.argv else seed()