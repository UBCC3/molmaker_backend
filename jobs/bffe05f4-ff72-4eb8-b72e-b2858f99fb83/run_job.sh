#!/bin/bash
#SBATCH --job-name=job_bffe05f4-ff72-4eb8-b72e-b2858f99fb83
#SBATCH --output=./jobs/bffe05f4-ff72-4eb8-b72e-b2858f99fb83/slurm.out
#SBATCH --error=./jobs/bffe05f4-ff72-4eb8-b72e-b2858f99fb83/slurm.err
#SBATCH --time=01:00:00
#SBATCH --mem=4G
#SBATCH --cpus-per-task=2

source ~/.bashrc
conda activate fastapi-env

python3 /qcengine_runner.py "single_point" "hf" "6-31g" "./jobs/bffe05f4-ff72-4eb8-b72e-b2858f99fb83/molecule.xyz" "./jobs/bffe05f4-ff72-4eb8-b72e-b2858f99fb83/result.json"
