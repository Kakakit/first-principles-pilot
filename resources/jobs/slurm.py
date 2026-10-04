"""Small, deterministic Slurm script renderer for one VASP attempt."""

import re
import shlex
from pathlib import Path


PROFILE_SCHEMA = "fpilot.slurm-site.v1"
SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class SlurmJobError(ValueError):
    pass


def load_profile(profile):
    if not isinstance(profile, dict):
        raise SlurmJobError("Slurm site profile must be an object")
    value = dict(profile)
    required = ("schema", "name", "partition", "node", "ntasks", "walltime",
                "vasp_executable", "module", "launcher", "environment")
    missing = [key for key in required if key not in value]
    if missing:
        raise SlurmJobError("Slurm profile is missing: {}".format(", ".join(missing)))
    if value["schema"] != PROFILE_SCHEMA:
        raise SlurmJobError("unsupported Slurm profile schema: {}".format(value["schema"]))
    for key in ("name", "partition", "node", "walltime", "vasp_executable", "module", "launcher"):
        item = value[key]
        if not isinstance(item, str) or not item.strip() or any(char in item for char in "\r\n\0"):
            raise SlurmJobError("Slurm profile field {} must be a non-empty single line".format(key))
    for key in ("name", "partition", "node", "launcher"):
        if not SAFE_TOKEN_RE.fullmatch(value[key]):
            raise SlurmJobError("Slurm profile field {} is not a safe token".format(key))
    if not re.fullmatch(r"[A-Za-z0-9_.+-]+/[A-Za-z0-9_.+-]+", value["module"]):
        raise SlurmJobError("Slurm module must be a registered module token")
    if value["launcher"] != "mpirun":
        raise SlurmJobError("only the validated Intel MPI Hydra launcher is supported")
    if not isinstance(value["ntasks"], int) or isinstance(value["ntasks"], bool) or value["ntasks"] <= 0:
        raise SlurmJobError("Slurm ntasks must be a positive integer")
    if not re.fullmatch(r"\d{2,3}:[0-5]\d:[0-5]\d", value["walltime"]):
        raise SlurmJobError("Slurm walltime must be HH:MM:SS")
    if not Path(value["vasp_executable"]).is_absolute():
        raise SlurmJobError("Slurm VASP executable must be an absolute path")
    environment = value["environment"]
    if not isinstance(environment, dict) or any(
        not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or
        not isinstance(item, str) or any(char in item for char in "\r\n\0")
        for key, item in environment.items()
    ):
        raise SlurmJobError("Slurm environment must contain safe string assignments")
    return value


def render_script(profile):
    profile = load_profile(profile)
    exports = "\n".join(
        "export {}={}".format(key, shlex.quote(value))
        for key, value in sorted(profile["environment"].items())
    )
    return """#!/usr/bin/env bash
#SBATCH --job-name={name}
#SBATCH --nodes=1
#SBATCH --nodelist={node}
#SBATCH --ntasks={ntasks}
#SBATCH --partition={partition}
#SBATCH --time={walltime}
#SBATCH --output=vasp.slurm.out
#SBATCH --error=vasp.slurm.err

set -euo pipefail
cd "${{SLURM_SUBMIT_DIR:?SLURM_SUBMIT_DIR is not set}}"
{exports}
source /etc/profile.d/lmod.sh
module load {module}
VASP_EXECUTABLE={vasp}
for f in INCAR KPOINTS POSCAR POTCAR; do test -s "$f"; done
test -x "$VASP_EXECUTABLE"
NPROCS="${{SLURM_NTASKS:?SLURM_NTASKS is not set}}"
echo "host=$(hostname) job=$SLURM_JOB_ID nprocs=$NPROCS vasp=$VASP_EXECUTABLE"
{launcher} -np "$NPROCS" "$VASP_EXECUTABLE" > vasp.out 2> vasp.err
test -s vasp.out
! grep -q 'PMI server not found' vasp.out
test "$(grep -c 'General timing and accounting informations' OUTCAR)" -eq 1
""".format(
        name=profile["name"], node=profile["node"], ntasks=profile["ntasks"],
        partition=profile["partition"], walltime=profile["walltime"], exports=exports,
        module=profile["module"], vasp=shlex.quote(profile["vasp_executable"]),
        launcher=profile["launcher"],
    )
