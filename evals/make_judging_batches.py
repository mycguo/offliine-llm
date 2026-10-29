"""Write the retrieval-test answers into batch files for independent AI judges.

Usage: python3 evals/make_judging_batches.py [--changed [N]]
Reads evals/results/*.jsonl (from retrieval_eval.py) and writes evals/judging/batch-N.md: for each
question, the official guidance it should match, then every model's answer and the sources it read.
Judging is blind: answers appear as "Answer A, B, ..." in a different order for each question, and
evals/judging/key.json maps each label back to its model.
--changed writes only the answers listed in evals/judging/changed.json (rerun after a harness fix) to
rejudge-B.md and key-rejudge.json, so just those get judged again; --changed N uses changed-N.json and writes
rejudge-N-B.md and key-rejudge-N.json (a later round).
"""
import json
import os
import random

ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(ROOT, "results")
OUT = os.path.join(ROOT, "judging")

# What official guidance says (USDA, CDC, FEMA/Ready.gov, EPA, NHS, WHO, Red Cross), for the judges.
GOLD = [
    "USDA: a closed fridge keeps food safe about 4 hours; a full freezer about 48 hours, half-full about 24. Discard perishables above 40°F (4°C) for 2+ hours. Never taste to check.",
    "Never run a generator in a garage, even with the door open (CO). Outdoors only, at least 20 feet from the house, away from windows, doors and vents. Have battery CO alarms.",
    "EPA: plain unscented bleach, 6%: 8 drops (about 1/8 tsp) per gallon; 8.25%: 6 drops. Double for cloudy water (filter first). Stir, wait 30 minutes; a slight chlorine smell means it worked. Older FEMA guidance says 16 drops; both are acceptable if consistent.",
    "CDC: a rolling boil for 1 minute; 3 minutes above 6,500 feet (2,000 m).",
    "CO poisoning: headache, dizziness, weakness, nausea/vomiting, chest pain, confusion. Get everyone into fresh air at once, call 911, don't go back in.",
    "Likely hypothermia: call 911; move somewhere warm; remove wet clothing; warm the core first (chest, neck, head, groin) with dry blankets or skin-to-skin; warm sweet non-alcoholic drinks only if alert; don't rub limbs; handle gently; CPR if not breathing.",
    "Severe bleeding: call 911; firm direct pressure with a clean cloth; if blood soaks through, add more on top without removing; tourniquet above the wound for life-threatening limb bleeding if pressure fails.",
    "Burn: cool under cool running water for 20 minutes (10+ acceptable); remove rings and tight items; cover loosely with cling film or a clean non-fluffy dressing; no ice, butter or creams; get care for large, deep or blistering burns, and burns to hands, face or genitals.",
    "Insulin: opened vials/pens are usually fine at room temperature (below about 86°F/30°C) for 28 days (some types 42-56); unopened can also be kept at room temperature up to the same limit. Keep out of heat and don't freeze. Check the label.",
    "Ready.gov: use flashlights or battery lanterns, not candles. If candles are used: never unattended, away from flammables, on a stable holder.",
    "Phone: Low Power Mode / battery saver; lower brightness; close apps; turn off radios or use airplane mode when not needed; text rather than call; charge from a power bank or a car running outdoors.",
    "Never run a car in a garage, even with the door open (CO). Charge in the car only outdoors with the exhaust clear, or use a power bank.",
    "Downed power line: stay at least 35 feet (10 m) away; assume it is live; don't touch anything it touches; call 911 and the utility; if in a vehicle touching it, stay inside unless there is a fire.",
    "No-flush toilet: line a bucket or the bowl with heavy plastic bags, add disinfectant (diluted bleach) or absorbent (sawdust, kitty litter), seal and store bags; or pour a bucket of water into the bowl to flush if water is available; wash hands.",
    "Keep warm: gather in one room and close it off, layer clothing, blankets, hats; never use an oven, stove, grill or generator for heat (CO); keep CO alarms working.",
    "Ready.gov: get inside the nearest building, go to the basement or middle, stay inside for 24 hours unless told otherwise, stay tuned to official information; remove outer clothing and wash if exposed.",
    "Floodwater: discard home-canned food and damaged commercial cans; undamaged commercial cans can be saved: remove labels, wash, sanitize (boil 2 minutes or bleach solution), relabel.",
    "Expired ibuprofen: usually safe but may be less effective; the FDA recommends not using expired medicine; ask a pharmacist when possible.",
    "WHO: 1 liter of clean water + 6 level teaspoons of sugar + half a level teaspoon of salt.",
    "Heat stroke is an emergency: call 911; move to a cool place; cool fast (cold water immersion, ice packs, wet cloths and fanning); don't give fluids if confused.",
    "Possible botulism: bulging, leaking or swollen containers, spurting liquid, foam, cloudy liquid, off smell. Never taste; discard safely (sealed bags), clean spills with bleach; get medical care for symptoms.",
    "Ready.gov: 1 gallon of water per person per day; at least a 3-day supply for evacuation, 2 weeks at home.",
]


def main(only_changed=False, round_tag=""):
    from retrieval_eval import QUESTIONS  # same order as GOLD
    changed = None
    if only_changed:
        changed = {(c["model"], c["q"]) for c in json.load(open(os.path.join(OUT, f"changed{round_tag}.json")))}
    assert len(QUESTIONS) == len(GOLD)
    answers = {}  # question -> [(model, answer, read titles)]
    for name in sorted(os.listdir(RESULTS)):
        if not name.endswith(".jsonl") or name == "summary.jsonl":
            continue
        model = name[:-6]
        rows = [json.loads(line) for line in open(os.path.join(RESULTS, name))]
        if len(rows) < len(QUESTIONS):
            print(f"skipping {model}: only {len(rows)} answers")
            continue
        for r in rows:
            if changed is not None and (model, r["q"]) not in changed:
                continue
            read = [e["title"] for e in r["events"] if e["type"] in ("read", "show")]
            answers.setdefault(r["q"], []).append((model, r["answer"], read))
    os.makedirs(OUT, exist_ok=True)
    if not only_changed:
        batches = [range(0, 6), range(6, 12), range(12, 17), range(17, 22)]
    else:
        batches = [range(0, 22)] if len(changed) <= 12 else [range(0, 11), range(11, 22)]
    prefix = f"rejudge{round_tag}" if only_changed else "batch"
    rng = random.Random(0)  # same shuffle every run, so key.json stays valid
    key = {}
    for b, idx in enumerate(batches):
        lines = []
        for i in idx:
            q = QUESTIONS[i]["q"]
            if not answers.get(q):
                continue
            lines += [f"## Question {i}: {q}", "", f"**Official guidance:** {GOLD[i]}", ""]
            shuffled = answers.get(q, [])[:]
            rng.shuffle(shuffled)
            for n, (model, answer, read) in enumerate(shuffled):
                label = chr(ord("A") + n)
                key[f"{i}{label}"] = model
                lines += [f"### Answer {label}", f"Sources it read: {'; '.join(read) or 'none'}", "",
                          answer.strip()[:3000], ""]
        with open(os.path.join(OUT, f"{prefix}-{b}.md"), "w") as f:
            f.write("\n".join(lines))
    with open(os.path.join(OUT, f"key-rejudge{round_tag}.json" if only_changed else "key.json"), "w") as f:
        json.dump(key, f, indent=1)
    print(f"wrote {len(batches)} batches; models: {sorted({m for v in answers.values() for m, _, _ in v})}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, ROOT)
    args = sys.argv[1:]
    tag = f"-{args[args.index('--changed') + 1]}" if "--changed" in args and args.index("--changed") + 1 < len(args) else ""
    main(only_changed="--changed" in args, round_tag=tag)
