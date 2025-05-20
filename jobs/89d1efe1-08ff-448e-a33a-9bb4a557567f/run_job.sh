#!/bin/bash
#SBATCH --job-name=job_89d1efe1-08ff-448e-a33a-9bb4a557567f
#SBATCH --output=./jobs/89d1efe1-08ff-448e-a33a-9bb4a557567f/slurm.out
#SBATCH --error=./jobs/89d1efe1-08ff-448e-a33a-9bb4a557567f/slurm.err
#SBATCH --time=01:00:00
#SBATCH --mem=4G
#SBATCH --cpus-per-task=2

source ~/.bashrc
source fastapi-env/bin/activate

python3 /qcengine_runner.py "single_point" "hf" "6-31g" "./jobs/89d1efe1-08ff-448e-a33a-9bb4a557567f/molecule.xyz" "./jobs/89d1efe1-08ff-448e-a33a-9bb4a557567f/result.json"
