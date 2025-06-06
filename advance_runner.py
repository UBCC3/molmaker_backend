#!/usr/bin/env python3

"""
advance_runner.py

Usage:
    python3 advance_runner.py submit <job_id> <xyz_file> <analysis_type> <method> <basis_set> <charge> <multiplicity>
    python3 advance_runner.py status <slurm_job_id>
    python3 advance_runner.py result <job_id>
    python3 advance_runner.py error  <job_id>
	python3 advance_runner.py cancel <slurm_job_id>
"""

import sys
import subprocess
import glob
from typing import Optional

def queue_job(job_id, xyz_file, analysis_type, method, basis_set, charge, multiplicity, keywords_json: Optional[str] = None):
    slurm_script = f"""#!/bin/bash
#SBATCH --job-name=qc-{job_id}
#SBATCH --output=test/qc-{job_id}.out
#SBATCH --error=test/qc-{job_id}.err
#SBATCH --time=00:15:00
#SBATCH --mem=4G

mkdir -p test

module load psi4
source env/bin/activate

python3 Cluster-API-QC/src/advance_analysis.py {job_id} {xyz_file} {analysis_type} {method} {basis_set} {charge} {multiplicity}"""
    if keywords_json is not None:
        slurm_script += f" {keywords_json}"

    script_path = f"slurm_job_{job_id}.sh"
    with open(script_path, 'w') as f:
        f.write(slurm_script)

    completed = subprocess.run(
        ['sbatch', script_path],
        check=True,
        capture_output=True,
        text=True
    )

    parts = completed.stdout.strip().split()
    slurm_id = parts[-1] if parts else ''
    print(slurm_id)
    return slurm_id

def get_status(slurm_id):
    try:
        out = subprocess.check_output([
            'sacct', '-n', '-j', slurm_id, '--format=State', '--parsable2'
        ], text=True)
        state = out.strip().split('\n')[0]
        state = state.split(' ')[0]
        print(state)
    except subprocess.CalledProcessError:
        print('UNKNOWN')

def get_result(job_id):
    pattern = f"result/{job_id}/result.json"
    files = glob.glob(pattern)
    if not files:
        print(f"No output found for job {job_id}")
        return
    with open(files[0]) as f:
        print(f.read())

def get_error(job_id):
    pattern = f"result/{job_id}/result.err"
    files = glob.glob(pattern)
    if not files:
        print(f"No error file found for job {job_id}")
        return
    with open(files[0]) as f:
        print(f.read())

def cancel_job(slurm_id):
    try:
        out = subprocess.check_output([
            'scancel', slurm_id
        ], text=True)
        # print(out)
        # check if the job is cancelled successfully
        out = subprocess.check_output([
            'sacct', '-n', '-j', slurm_id, '--format=State', '--parsable2'
        ], text=True)
        status = out.strip().split('\n')[0]
        success = status.split(' ')[0]
        if success == 'CANCELLED':
            print("CANCELLED")
        else:
            print("FAILED")
    except subprocess.CalledProcessError:
        print(f"FALIED")

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == 'submit' and len(sys.argv) >= 8:
        _, _, xyz_file, *rest = sys.argv
        job_id = rest[0]
        analysis_type = rest[1]
        method = rest[2]
        basis_set = rest[3]
        charge = rest[4]
        multiplicity = rest[5]
        keywords_json = rest[6] if len(sys.argv) > 9 else None
        queue_job(job_id, xyz_file, analysis_type, method, basis_set, charge, multiplicity, keywords_json)
    elif cmd == 'status' and len(sys.argv) == 3:
        get_status(sys.argv[2])
    elif cmd == 'result' and len(sys.argv) == 3:
        get_result(sys.argv[2])
    elif cmd == 'error' and len(sys.argv) == 3:
        get_error(sys.argv[2])
    elif cmd == 'cancel' and len(sys.argv) == 3:
        cancel_job(sys.argv[2])
    else:
        print(f"Unknown or malformed command: {' '.join(sys.argv)}")
        print(__doc__)
        sys.exit(1)

if __name__ == '__main__':
    main()
