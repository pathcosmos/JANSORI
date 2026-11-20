"""Latest requested horizon. Bound by SHA in every new execution contract.

The live original57k producer does not import this module. Changing this file
invalidates prepared bridge contracts and requires new validation/admission.
"""
TARGET_STEPS = 35_000
TOKENS_PER_STEP = 2_097_152
TOTAL_TOKENS = TARGET_STEPS * TOKENS_PER_STEP
DECAY_START_STEP = TARGET_STEPS * 85 // 100
FINAL_CHECKPOINT = f'ckpt_{TARGET_STEPS:07d}'
NATIVE_KEEP = TARGET_STEPS + 1
LABEL = f'{TARGET_STEPS // 1000}k'


def generation_schedule(original):
    return sorted({s for s in original if s <= TARGET_STEPS} | {TARGET_STEPS})
