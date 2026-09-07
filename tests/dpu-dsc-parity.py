#!/usr/bin/env python3
"""Compile actual locked kernel C before/after the one-line DSC candidate.

No device access, installation or display/sleep request. Generated compilation
files live only in TemporaryDirectory. Pass the preserved v3 dpu_rm.c.
"""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile

BASELINE_SHA = "aab48d26ed2a7413d7944e84068e07150a469073573f8a0fb4da628cf8ec2851"
ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "tools/kernel-ab/patches/0004-dpu-dsc-skip-incompatible-parity.patch"
OLD = "\t\tret = _dpu_rm_pingpong_dsc_check(dsc_idx, pp_idx);\n\t\tif (ret)\n\t\t\treturn -ENAVAIL;"
NEW = OLD.replace("return -ENAVAIL;", "continue;")

PREAMBLE = r'''
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define ENAVAIL 119
#define PINGPONG_0 1
#define PINGPONG_MAX 9
#define ARRAY_SIZE(a) ((int)(sizeof(a) / sizeof((a)[0])))
#define DPU_ERROR(...) ((void)0)
struct dpu_rm { void *dsc_blks[4]; };
struct dpu_global_state {
    uint32_t pingpong_to_crtc_id[8];
    uint32_t dsc_to_crtc_id[4];
};
struct msm_display_topology { int num_dsc; };
static bool reserved_by_other(uint32_t *map, int i, uint32_t id)
{ return map[i] && map[i] != id; }
'''

MAIN = r'''
int main(void)
{
    unsigned cases = 0, baseline_misses = 0;
    for (unsigned pp_mask = 0; pp_mask < 256; ++pp_mask)
    for (unsigned present = 0; present < 16; ++present)
    for (unsigned reserved = 0; reserved < 16; ++reserved)
    for (int need = 1; need <= 4; ++need) {
        struct dpu_rm rm = {0};
        struct dpu_global_state before = {0};
        struct msm_display_topology top = { .num_dsc = need };
        for (int i = 0; i < 8; ++i)
            before.pingpong_to_crtc_id[i] = pp_mask & (1u << i) ? 1 : 2;
        for (int i = 0; i < 4; ++i) {
            rm.dsc_blks[i] = present & (1u << i) ? (void *)(uintptr_t)1 : NULL;
            before.dsc_to_crtc_id[i] = reserved & (1u << i) ? 2 : 0;
        }
        /* Ordered pairing oracle, independently walks requested pingpongs. */
        int allocated = 0, next_dsc = 0;
        struct dpu_global_state expected = before;
        for (int pp = 0; pp < 8 && allocated < need; ++pp) {
            if (!(pp_mask & (1u << pp)))
                continue;
            int chosen = -1;
            for (int d = next_dsc; d < 4; ++d) {
                if ((present & (1u << d)) && !(reserved & (1u << d)) &&
                    (d % 2) == (pp % 2)) { chosen = d; break; }
            }
            if (chosen < 0)
                break;
            expected.dsc_to_crtc_id[chosen] = 1;
            next_dsc = chosen + 1;
            ++allocated;
        }
        const int want = allocated == need ? 0 : -ENAVAIL;
        struct dpu_global_state old = before, fixed = before;
        int old_rc = baseline_dsc_alloc(&rm, &old, 1, &top);
        int fixed_rc = candidate_dsc_alloc(&rm, &fixed, 1, &top);
        assert(fixed_rc == want);
        assert(!memcmp(&fixed, &expected, sizeof(fixed)));
        assert(!memcmp(fixed.pingpong_to_crtc_id, before.pingpong_to_crtc_id,
                       sizeof(fixed.pingpong_to_crtc_id)));
        for (int i = 0; i < 4; ++i)
            if (reserved & (1u << i)) assert(fixed.dsc_to_crtc_id[i] == 2);
        if (!old_rc) { assert(!fixed_rc); assert(!memcmp(&old, &fixed, sizeof(old))); }
        if (old_rc && !fixed_rc) ++baseline_misses;
        ++cases;
    }
    /* Exact lower-first topology lead: PP0 belongs to lower, upper owns PP1. */
    struct dpu_rm rm = { .dsc_blks = {(void *)1, (void *)1, (void *)1, (void *)1} };
    struct dpu_global_state old = { .pingpong_to_crtc_id = {2, 1} }, fixed = old;
    struct msm_display_topology top = { .num_dsc = 1 };
    assert(baseline_dsc_alloc(&rm, &old, 1, &top) == -ENAVAIL);
    assert(candidate_dsc_alloc(&rm, &fixed, 1, &top) == 0);
    assert(fixed.dsc_to_crtc_id[0] == 0 && fixed.dsc_to_crtc_id[1] == 1);
    /* A resource already owned by this CRTC may be retained. */
    fixed = (struct dpu_global_state){ .pingpong_to_crtc_id = {2, 1},
                                       .dsc_to_crtc_id = {2, 1} };
    assert(candidate_dsc_alloc(&rm, &fixed, 1, &top) == 0);
    assert(fixed.dsc_to_crtc_id[0] == 2 && fixed.dsc_to_crtc_id[1] == 1);
    assert(baseline_misses > 0);
    printf("PASS: %u matrix cases; %u false baseline rejections fixed; "
           "all baseline successes unchanged; lower-first and ownership cases passed\n",
           cases, baseline_misses);
}
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    blob = args.source.read_bytes()
    if hashlib.sha256(blob).hexdigest() != BASELINE_SHA:
        raise SystemExit("Refusing an unrecognized baseline dpu_rm.c")
    original = blob.decode()
    if original.count(OLD) != 1:
        raise SystemExit("Baseline target differs")
    start = original.index("static int _dpu_rm_pingpong_next_index(")
    end = original.index("static int _dpu_rm_dsc_alloc_pair(", start)
    functions = original[start:end]
    with tempfile.TemporaryDirectory(prefix="pds-dsc-test-") as tmp:
        root = Path(tmp)
        target = root / "drivers/gpu/drm/msm/disp/dpu1/dpu_rm.c"
        target.parent.mkdir(parents=True)
        target.write_bytes(blob)
        subprocess.run(["patch", "--batch", "-p1", "-i", str(PATCH)], cwd=root, check=True,
                       timeout=15)
        fixed = target.read_text()
        if fixed != original.replace(OLD, NEW):
            raise SystemExit("Patch changed more than the one permitted branch")
        baseline_c = functions.replace("_dpu_rm_", "baseline_")
        candidate_c = functions.replace(OLD, NEW).replace("_dpu_rm_", "candidate_")
        test_c = root / "test.c"
        test_c.write_text(PREAMBLE + baseline_c + candidate_c + MAIN)
        output = root / "test"
        subprocess.run([args.cc, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                        str(test_c), "-o", str(output)], check=True, timeout=60)
        subprocess.run([str(output)], check=True, timeout=30)
    print("candidate_source_sha256=" + hashlib.sha256(fixed.encode()).hexdigest())


if __name__ == "__main__":
    main()
