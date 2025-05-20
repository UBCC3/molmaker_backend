#!/bin/bash
#SBATCH --job-name=job_6f416224-d82c-45d8-9f4f-1774287f296e
#SBATCH --output=./jobs/6f416224-d82c-45d8-9f4f-1774287f296e/slurm.out
#SBATCH --error=./jobs/6f416224-d82c-45d8-9f4f-1774287f296e/slurm.err
#SBATCH --time=01:00:00
#SBATCH --mem=4G
#SBATCH --cpus-per-task=2

source ~/.bashrc
source fastapi-env/bin/activate

python3 /qcengine_runner.py "single_point" "hf" "6-31g" "./jobs/6f416224-d82c-45d8-9f4f-1774287f296e/molecule.xyz" "./jobs/6f416224-d82c-45d8-9f4f-1774287f296e/result.json"
