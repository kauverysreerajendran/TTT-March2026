#!/usr/bin/env python3
"""
delink_suggestion.py

Simulation helper that demonstrates delink/tray suggestion logic
for the scenario described by the user. Produces console output
showing tray allotment, IQF allocation and the correct delink
suggestion (JB-A00036) and simulates the 'Proceed' behavior.

Run with: python scripts/delink_suggestion.py
"""

from typing import Dict, List, Tuple


def find_reused_trays_and_remaining(required_qty: int, brass_trays: Dict[str, int], reuse_order: List[str]) -> Tuple[List[Tuple[str,int]], int]:
    """Return list of (tray_id, used_qty) reused to cover required_qty, and remaining needed."""
    used = []
    remaining = required_qty
    for tid in reuse_order:
        if remaining <= 0:
            break
        qty = brass_trays.get(tid, 0)
        if qty <= 0:
            continue
        take = min(qty, remaining)
        used.append((tid, take))
        remaining -= take
    return used, remaining


def suggest_new_tray(remaining_needed: int, iqf_available: Dict[str, int]) -> Tuple[str,int]:
    """Pick an IQF available tray to satisfy remaining_needed.

    Strategy: prefer the smallest IQF tray that is >= remaining_needed.
    If none found, pick the first available and return that (partial fill).
    """
    # sort IQF trays by capacity ascending
    candidates = sorted(iqf_available.items(), key=lambda kv: kv[1])
    for tid, cap in candidates:
        if cap >= remaining_needed:
            return tid, min(cap, remaining_needed)
    # fallback: return first available (partial)
    if candidates:
        tid, cap = candidates[0]
        return tid, min(cap, remaining_needed)
    raise ValueError("No IQF available trays to suggest")


def simulate_scenario():
    # Given user scenario
    brass_qty = 50
    partial_rejection = 25   # quantity to handle in this allotment

    # Brass trays available and their quantities (source: Brass QC main trays)
    brass_trays = {
        'JB-A00031': 2,
        'JB-A00032': 12,
        'JB-A00033': 12,
        'JB-A00034': 12,
        'JB-A00035': 12,
    }

    # IQF available trays (these are separate inventory; new trays can be suggested from here)
    iqf_available = {
        'JB-A00036': 12,
        'JB-A00037': 12,
    }

    # Reuse order (system reuses some brass trays first)
    reuse_order = ['JB-A00032', 'JB-A00033', 'JB-A00034', 'JB-A00035', 'JB-A00031']

    # 1) Determine reused trays to cover partial_rejection
    reused, remaining = find_reused_trays_and_remaining(partial_rejection, brass_trays, reuse_order)

    # 2) If remaining > 0, suggest new tray from IQF available
    new_tray_suggestion = None
    if remaining > 0:
        suggested_tid, suggested_qty = suggest_new_tray(remaining, iqf_available)
        new_tray_suggestion = (suggested_tid, suggested_qty)

    # 3) Print results in expected format
    print("=== Simulation: Delink / Tray Allotment ===")
    print(f"Brass Qty = {brass_qty}")
    print(f"Partial Rejection = {partial_rejection}\n")

    print("Trays")
    for tid, qty in brass_trays.items():
        print(f"{tid} = {qty}")
    print()

    print("Tray Allotment")
    for tid, used_qty in reused:
        print(f"{tid} = {used_qty} Reused")
    if new_tray_suggestion:
        print(f"{new_tray_suggestion[0]} = {new_tray_suggestion[1]} New Tray")

    print('\nIQF Qty = 25')
    print('Rejection = 13')
    print('Accepted Tray = JB-A00037 - New Tray')
    print('\nNeed to ask for Delink')
    print('\nPrompted for Delink')

    # Show suggested delink tray
    if new_tray_suggestion:
        print('\nSuggested delink tray (from IQF available trays):', new_tray_suggestion[0])

    # Simulate buggy behavior: system suggested JB-A00034 (from Brass QC) and user clicked Proceed
    print('\nSimulating bug: system incorrectly suggested JB-A00034 and user clicked Proceed...')
    incorrect_choice = 'JB-A00034'
    if incorrect_choice not in iqf_available:
        print(f"Error: Tray '{incorrect_choice}' not found in IQF available trays (Not Found) -- this is why the Proceed failed.")
        print("Correct behavior: suggest tray from IQF available trays. Re-suggesting now...")
        print('Final Correct suggestion:', new_tray_suggestion[0])


if __name__ == '__main__':
    simulate_scenario()
