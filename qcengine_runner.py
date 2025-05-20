import sys
import json
import qcengine as qcng
import qcelemental as qcel
from qcelemental.util import serialize
import traceback

def main():
    try:
        # Read command-line arguments
        calculation_type = sys.argv[1]
        program = sys.argv[2]
        method = sys.argv[3]
        basis_set = sys.argv[4]
        input_path = sys.argv[5]
        result_path = sys.argv[6]

        # Load molecule from file
        mol = qcel.models.Molecule.from_file(input_path, dtype="xyz")

        # Build input for QCEngine
        input_data = qcel.models.AtomicInput(
            molecule=mol,
            driver="energy" if calculation_type == "single_point" else calculation_type,
            model={"method": method, "basis": basis_set},
            keywords={"scf_type": "df"}
        )

        # Run QCEngine
        result = qcng.compute(input_data, program=program)

        # Serialize and save result
        with open(result_path, "w") as f:
            f.write(serialize(result))

        # Print success flag to stdout (useful for logs)
        print(f"Job completed successfully: {result.success}")

    except Exception as e:
        print("Error running QCEngine job:")
        traceback.print_exc()
        with open(result_path, "w") as f:
            f.write(json.dumps({
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc()
            }))


if __name__ == "__main__":
    main()
