#!/bin/bash
#SBATCH --job-name=job_79b2cde2-55a8-456e-9fc9-460fd8501592
#SBATCH --output=./jobs/79b2cde2-55a8-456e-9fc9-460fd8501592/slurm.out
#SBATCH --error=./jobs/79b2cde2-55a8-456e-9fc9-460fd8501592/slurm.err
#SBATCH --time=01:00:00
#SBATCH --mem=4G
#SBATCH --cpus-per-task=2

source ~/.bashrc
conda activate fastapi-env

python3 /qcengine_runner.py "single_point" "b3lyp" "sto-3g" "./jobs/79b2cde2-55a8-456e-9fc9-460fd8501592/molecule.xyz" "./jobs/79b2cde2-55a8-456e-9fc9-460fd8501592/result.json"
