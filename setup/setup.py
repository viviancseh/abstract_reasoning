import os
import platform
from pathlib import Path
from typing import Union, List, Dict
from icecream import ic
from PIL import Image
import json
import shutil
import matplotlib.pyplot as plt
import pandas as pd
from math import factorial
from itertools import combinations, permutations
import random
import numpy as np
from tqdm.auto import tqdm
import concurrent.futures
import multiprocessing

WD = Path(__file__).parent
os.chdir(WD)
from utils import get_monitors_info


# from database import Database
def get_mapping(items, sort_items=True, both=True) -> List[Dict]:
    if sort_items:
        items = sorted(items)

    mapping = [{item: i for i, item in enumerate(items)}]

    if both:
        mapping.append({v: k for k, v in mapping[0].items()})

    return mapping


def get_sequences(save_dir: Union[str, Path], icons=None, patterns=None):
    #! TEMP
    save_dir = WD

    # if icons is not None or patterns is not None:
    #     raise NotImplementedError("Custom icons and patterns are not yet supported.")

    # icons = [
    #     "wheat",
    #     "key",
    #     "smile",
    #     "heart",
    #     "eye",
    #     "bulb",
    #     "carrot",
    #     "helicopter",
    #     "truck",
    #     "guitar",
    #     "cube",
    #     "star",
    #     "hammer",
    #     "bell",
    #     "sun",
    #     "pyramid",
    # ]
    icons = [
        "and",
        "angle-up",
        "asterisk",
        "at",
        "dollar-sign",
        "equals",
        "exclamation",
        "greater-than",
        "hashtag",
        "less-than",
        "minus",
        "percent",
        "plus",
        "quote",
        "slash",
        "tilde",
    ]

    patterns = [
        ["AAAB AAA", "B"],
        ["ABAB CDC", "D"],
        ["ABBA ABB", "A"],
        ["ABBA CDD", "C"],
        ["ABBC ABB", "C"],
        ["ABCA ABC", "A"],
        ["ABCD DCB", "A"],
        ["ABCD EED", "C"],
        ["ABCD EDC", "B"],
        ["ABCA BCA", "B"],
        ["ABCD EFG", "H"],
    ]

    save_dir = Path(save_dir / "sequences")
    save_dir.mkdir(exist_ok=True, parents=True)

    _patterns = ["".join(p).replace(" ", "") for p in patterns]
    # * Determine the maximum number of unique characters in any pattern
    # all_letters = set(sorted("".join(_patterns)))

    stim_IDs, stim_IDs_inv = get_mapping(icons)
    pattern_IDs, pattern_IDs_inv = get_mapping(_patterns)

    with open(save_dir / "items_mapping.json", "w") as f:
        json.dump({"stim_IDs": stim_IDs, "pattern_IDs": pattern_IDs}, f, indent=4)

    n_icons = len(icons)
    icons_int = stim_IDs.values()

    # * For each pattern, work out exactly how many *distinct* valid sequences
    # * it could ever produce, using the permutation formula (n! / (n-k)!)
    # * instead of generating and storing them all. n is the number of icons, k is the number of unique letters in the pattern.
    pattern_k = {p: len(set(p)) for p in _patterns}
    pattern_total_combs = {
        p: factorial(n_icons) // factorial(n_icons - k) for p, k in pattern_k.items()
    }

    # * Balance quota: every pattern contributes the same number of sequences,
    # * capped by whichever pattern has the fewest possible options.
    min_n_patterns = min(pattern_total_combs.values())
    print(
        "Possible sequences per pattern:",
        {p: f"{c:,}" for p, c in pattern_total_combs.items()},
    )
    print(f"Balance quota (min across patterns): {min_n_patterns:,}")

    # * Directly, randomly sample `min_n_patterns` *distinct* sequences per
    # * pattern (rejection sampling).
    cols = [f"figure{i}" for i in range(1, 9)] + ["pattern"]
    selected_rows = []

    for pattern in tqdm(_patterns, desc="Sampling per pattern"):
        sorted_letters = sorted(set(pattern))
        k = pattern_k[pattern]
        seen = set()

        while len(seen) < min_n_patterns:
            sample_icons = random.sample(icons, k)
            icon_dict = dict(zip(sorted_letters, sample_icons))
            comb = [icon_dict[char] for char in pattern]
            comb = tuple(stim_IDs[name] for name in comb)
            seen.add(comb)

        for comb in seen:
            selected_rows.append(comb + (pattern_IDs[pattern],))

    selected_combs = pd.DataFrame(selected_rows, columns=cols)
    selected_combs.to_csv(save_dir / "all_combinations.csv", index=False)

    n_per_pattern = 20
    n_sessions = 5
    # solution_inds = [-1] * (len(patterns) * n_per_pattern)
    solution_idx = 7

    sequences = []

    for i in range(n_sessions):
        sequences_i = selected_combs.groupby("pattern").sample(n_per_pattern)
        selected_combs.drop(sequences_i.index, inplace=True)

        patterns = sequences_i.pop("pattern")
        choice_cols = [f"choice{i + 1}" for i in range(4)] + [
            "solution",
            "masked_idx",
            "seq_order",
            "choice_order",
        ]
        choices = pd.DataFrame(columns=choice_cols)

        for idx, seq in sequences_i.iterrows():
            seq = list(seq)
            unique_items = list(set(seq))
            solution = seq[solution_idx]

            n = len(unique_items)

            if n > 4:
                seq_choices = [solution] + random.sample(
                    [i for i in unique_items if i != solution], k=3
                )
            elif n <= 4:
                seq_choices = unique_items + random.sample(
                    [i for i in icons_int if i not in unique_items], k=4 - n
                )

            seq_choices = random.sample(seq_choices, len(seq_choices))

            seq_order = "".join([str(i) for i in random.sample(range(8), 8)])
            choice_order = "".join([str(i) for i in random.sample(range(4), 4)])

            seq_choices.extend([solution, solution_idx, seq_order, choice_order])
            seq_choices = pd.DataFrame([seq_choices], columns=choice_cols, index=[idx])

            choices = pd.concat([choices, seq_choices], axis=0)
            # choices.reset_index(drop=True, inplace=True)

        sequences_i = pd.concat([sequences_i, choices], axis=1)

        sequences_i = sequences_i.astype("object")
        sequences_i.iloc[:, :13] = sequences_i.iloc[:, :13].replace(stim_IDs_inv)
        sequences_i["masked_idx"] = sequences_i["masked_idx"].astype(int)
        sequences_i["pattern"] = patterns.replace(pattern_IDs_inv)
        sequences_i["trial_type"] = "experiment"
        sequences_i.reset_index(drop=False, inplace=True, names=["item_id"])
        sequences_i.to_csv(save_dir / f"session_{i + 1}.csv", index=False)

        sequences.append(sequences_i)

    return sequences


# def process_permutation(args):
#     perm, _patterns, all_letters, stim_IDs, pattern_IDs = args
#     icon_dict = dict(zip(sorted(all_letters), perm))
#     combs = []
#     for pattern in _patterns:
#         comb = [icon_dict[char] for char in pattern]
#         comb = [stim_IDs[name] for name in comb]
#         comb += [pattern_IDs[pattern]]
#         combs.append(tuple(comb))
#     return combs


def get_practice_sequences(save_dir: Union[str, Path], icons=None, n_practice=2):
    #! TEMP
    save_dir = WD

    icons = [
        "and",
        "angle-up",
        "asterisk",
        "at",
        "dollar-sign",
        "equals",
        "exclamation",
        "greater-than",
        "hashtag",
        "less-than",
        "minus",
        "percent",
        "plus",
        "quote",
        "slash",
        "tilde",
    ]

    # * Deliberately different from the 11 real experiment patterns, so
    # * practice trials never preview an actual test pattern.
    patterns = [
        ["AABB AAB", "B"],
        ["AABC AAB", "C"],
        ["ABCD ABC", "D"],
    ]

    save_dir = Path(save_dir / "sequences")
    save_dir.mkdir(exist_ok=True, parents=True)

    _patterns = ["".join(p).replace(" ", "") for p in patterns]

    stim_IDs, stim_IDs_inv = get_mapping(icons)
    pattern_IDs, pattern_IDs_inv = get_mapping(_patterns)
    icons_int = stim_IDs.values()

    # * Same direct-sampling approach as get_sequences(): generate exactly
    # * the handful of examples needed per pattern, instead of enumerating
    # * everything first.
    cols = [f"figure{i}" for i in range(1, 9)] + ["pattern"]
    selected_rows = []

    for pattern in _patterns:
        sorted_letters = sorted(set(pattern))
        k = len(sorted_letters)
        seen = set()

        while len(seen) < n_practice:
            sample_icons = random.sample(icons, k)
            icon_dict = dict(zip(sorted_letters, sample_icons))
            comb = [icon_dict[char] for char in pattern]
            comb = tuple(stim_IDs[name] for name in comb)
            seen.add(comb)

        for comb in seen:
            selected_rows.append(comb + (pattern_IDs[pattern],))

    selected_combs = pd.DataFrame(selected_rows, columns=cols)
    patterns_col = selected_combs.pop("pattern")

    # * Build choice options for each practice sequence -- identical logic
    # * to the per-session loop in get_sequences().
    solution_idx = 7
    choice_cols = [f"choice{i + 1}" for i in range(4)] + [
        "solution",
        "masked_idx",
        "seq_order",
        "choice_order",
    ]
    choices = pd.DataFrame(columns=choice_cols)

    for idx, seq in selected_combs.iterrows():
        seq = list(seq)
        unique_items = list(set(seq))
        solution = seq[solution_idx]

        n = len(unique_items)

        if n > 4:
            seq_choices = [solution] + random.sample(
                [i for i in unique_items if i != solution], k=3
            )
        elif n <= 4:
            seq_choices = unique_items + random.sample(
                [i for i in icons_int if i not in unique_items], k=4 - n
            )

        seq_choices = random.sample(seq_choices, len(seq_choices))

        seq_order = "".join([str(i) for i in random.sample(range(8), 8)])
        choice_order = "".join([str(i) for i in random.sample(range(4), 4)])

        seq_choices.extend([solution, solution_idx, seq_order, choice_order])
        seq_choices = pd.DataFrame([seq_choices], columns=choice_cols, index=[idx])

        choices = pd.concat([choices, seq_choices], axis=0)

    practice_df = pd.concat([selected_combs, choices], axis=1)

    practice_df = practice_df.astype("object")
    practice_df.iloc[:, :13] = practice_df.iloc[:, :13].replace(stim_IDs_inv)
    practice_df["masked_idx"] = practice_df["masked_idx"].astype(int)
    practice_df["pattern"] = patterns_col.replace(pattern_IDs_inv)
    practice_df["trial_type"] = "practice"
    practice_df.reset_index(drop=False, inplace=True, names=["item_id"])
    practice_df.to_csv(save_dir / "practice_sequences.csv", index=False)

    return practice_df


"""
def process_permutation(args):
    perm, _patterns, all_letters, stim_IDs, pattern_IDs = args
    icon_dict = dict(zip(sorted(all_letters), perm))
    combs = []
    for pattern in _patterns:
        comb = [icon_dict[char] for char in pattern]
        comb = [stim_IDs[name] for name in comb]
        comb += [pattern_IDs[pattern]]
        combs.append(tuple(comb))
    return combs


def get_sequences_mpcsing(save_dir: Union[str, Path], icons=None, patterns=None):
    save_dir = Path(save_dir) / "sequences2"
    save_dir.mkdir(exist_ok=True, parents=True)

    icons = [
        "wheat",
        "key",
        "smile",
        "heart",
        "eye",
        "bulb",
        "carrot",
        "helicopter",
        "truck",
        "guitar",
        "cube",
        "star",
        "hammer",
        "bell",
        "sun",
        "pyramid",
    ]

    # patterns = [
    #     ["AAAB AAA", "B"],
    #     ["ABAB CDC", "D"],
    #     ["ABBA ABB", "A"],
    #     ["ABBA CDC", "D"],
    #     ["ABBC ABB", "C"],
    #     ["ABCA ABC", "A"],
    #     ["ABCD DCB", "A"],
    #     ["ABCDE ED", "C"],
    # ]
    patterns = [
        ["AAAA AAA", "A"],
        ["AAAB AAA", "B"],
        ["AABB AAB", "B"],
        ["ABBB ABB", "B"],
        ["AABC AAB", "C"],
        ["ABCC ABC", "C"],
        ["ABBC ABB", "C"],
        ["ABAB ABA", "B"],
        ["ABBA ABB", "A"],
        ["ABCA ABC", "A"],
        ["ABAC ABA", "C"],
        ["ABAB CDC", "D"],
        ["AABB CCD", "D"],
        ["ABCD ABC", "D"],
        ["ABBA CDD", "C"],
        ["ABCD DCB", "A"],
        ["ABCD EAB", "C"],
        ["ABCD EFA", "B"],
        ["ABCD EFG", "H"],
        ["ABCD EED", "C"],
        ["ABC CBA A", "B"],
    ]
    _patterns = ["".join(p).replace(" ", "") for p in patterns]
    all_letters = set(sorted("".join(_patterns)))

    stim_IDs, stim_IDs_inv = get_mapping(icons)
    pattern_IDs, pattern_IDs_inv = get_mapping(_patterns)

    with open(save_dir / "items_mapping.json", "w") as f:
        json.dump({"stim_IDs": stim_IDs, "pattern_IDs": pattern_IDs}, f, indent=4)

    n_icons = len(icons)
    n_perms = int(factorial(n_icons) / factorial(n_icons - len(all_letters)))
    icons_int = stim_IDs.values()
    icon_permutations = list(permutations(icons, len(all_letters)))

    args = [
        (perm, _patterns, all_letters, stim_IDs, pattern_IDs)
        for perm in icon_permutations
    ]

    all_combs = set()
    with concurrent.futures.ProcessPoolExecutor(
        mp_context=multiprocessing.get_context("fork")
    ) as executor:
        futures = [executor.submit(process_permutation, arg) for arg in args]
        for future in tqdm(
            concurrent.futures.as_completed(futures),
            total=n_perms,
            desc="Processing permutations",
        ):
            all_combs.update(future.result())

    n_combs = len(all_combs)
    print(f"{n_combs:,} unique combinations generated.")

    cols = [f"figure{i}" for i in range(1, 9)] + ["pattern"]
    all_combs = pd.DataFrame(all_combs, columns=cols)

    all_combs.to_csv(save_dir / "all_combinations.csv", index=False)

    min_n_patterns = all_combs["pattern"].value_counts().min()
    selected_combs = all_combs.groupby("pattern").sample(min_n_patterns)

    n_per_pattern = 10
    n_sessions = 5
    solution_idx = 7

    sequences = []

    for i in range(n_sessions):
        sequences_i = selected_combs.groupby("pattern").sample(n_per_pattern)
        selected_combs.drop(sequences_i.index, inplace=True)

        patterns = sequences_i.pop("pattern")
        choice_cols = [f"choice{i + 1}" for i in range(4)] + [
            "solution",
            "masked_idx",
            "seq_order",
            "choice_order",
        ]
        choices = pd.DataFrame(columns=choice_cols)

        for idx, seq in sequences_i.iterrows():
            seq = list(seq)
            unique_items = list(set(seq))
            solution = seq[solution_idx]

            n = len(unique_items)

            if n > 4:
                seq_choices = [solution] + random.sample(
                    [i for i in unique_items if i != solution], k=3
                )
            elif n <= 4:
                seq_choices = unique_items + random.sample(
                    [i for i in icons_int if i not in unique_items], k=4 - n
                )

            seq_choices = random.sample(seq_choices, len(seq_choices))

            seq_order = "".join([str(i) for i in random.sample(range(8), 8)])
            choice_order = "".join([str(i) for i in random.sample(range(4), 4)])

            seq_choices.extend([solution, solution_idx, seq_order, choice_order])
            seq_choices = pd.DataFrame([seq_choices], columns=choice_cols, index=[idx])

            choices = pd.concat([choices, seq_choices], axis=0)

        sequences_i = pd.concat([sequences_i, choices], axis=1)

        sequences_i = sequences_i.astype("object")
        sequences_i.iloc[:, :13] = sequences_i.iloc[:, :13].replace(stim_IDs_inv)
        sequences_i["masked_idx"] = sequences_i["masked_idx"].astype(int)
        sequences_i["pattern"] = patterns.replace(pattern_IDs_inv)
        sequences_i.reset_index(drop=False, inplace=True, names=["item_id"])
        sequences_i.to_csv(save_dir / f"session_{i + 1}.csv", index=False)

        sequences.append(sequences_i)

    return sequences
"""


def change_original_seq_masked_idx(
    original_sequences: pd.DataFrame, masked_idx: int
) -> pd.DataFrame:
    import re

    new_sequences = original_sequences.copy()
    new_sequences.reset_index(drop=True, inplace=True)

    seq_cols = [c for c in new_sequences.columns if re.search(r"figure\d", c)]
    choice_cols = [c for c in new_sequences.columns if re.search(r"choice\d", c)]

    unique_icons = tuple(set(np.concat(new_sequences[seq_cols + choice_cols].values)))

    new_sequences["masked_idx"] = masked_idx
    solutions = new_sequences[f"figure{masked_idx + 1}"]
    new_sequences["solution"] = solutions

    for idx, row in new_sequences.iterrows():
        seq = list(row[seq_cols])
        unique_items = list(set(seq))
        solution = row["solution"]

        n = len(unique_items)

        if n > 4:
            seq_choices = [solution] + random.sample(
                [i for i in unique_items if i != solution], k=3
            )
        elif n <= 4:
            seq_choices = unique_items + random.sample(
                [i for i in unique_icons if i not in unique_items], k=4 - n
            )

        seq_choices = random.sample(seq_choices, len(seq_choices))
        new_sequences.loc[idx, choice_cols] = seq_choices

    choices = new_sequences[choice_cols].values
    solutions = new_sequences["solution"].values
    masked_icons = new_sequences[f"figure{masked_idx + 1}"].values

    assert all([solutions[i] in choices[i] for i in range(len(solutions))])
    assert all([masked_icons[i] == solutions[i] for i in range(len(masked_icons))])
    assert all([masked_icons[i] in choices[i] for i in range(len(masked_icons))])

    return new_sequences


if __name__ == "__main__":
    origl_sequences = [
        WD.parent / f"config/sequences/session_{i}.csv" for i in range(1, 6)
    ]
    origl_sequences = pd.concat([pd.read_csv(f) for f in origl_sequences])

    new_sequences = []
    for masked_idx in range(7):
        new_sequences.append(
            change_original_seq_masked_idx(origl_sequences, masked_idx)
        )
    new_sequences = pd.concat(new_sequences, ignore_index=True)
    new_sequences = pd.concat([origl_sequences, new_sequences], ignore_index=True)

    new_sequences.reset_index(drop=True, inplace=True)

    for i, row in new_sequences.iterrows():
        assert row["solution"] == row[f"figure{row['masked_idx'] + 1}"]
        assert row["solution"] in row[[f"choice{i + 1}" for i in range(4)]].values

    # new_sequences.to_csv(WD / f"sessions-1_to_5-masked_idx(mixed).csv", index=False)
