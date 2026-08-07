// Flow corpus (Go). Top-level functions with a known call graph.
// Ground truth call edges are in ../ground_truth.json. Every callee has a single
// definition so name-based resolution is unambiguous.

package app

func HandleRequest() {
	ValidateInput()
	result := Compute()
	WriteResponse(result)
}

func ValidateInput() {
	CheckSchema()
	CheckLimits()
}

func CheckSchema() {
	Normalize()
}

func CheckLimits() {
	Normalize()
}

func Normalize() int {
	return 1
}

func Compute() int {
	total := Aggregate()
	return Finalize(total)
}

func Aggregate() int {
	LoadRows()
	return ReduceRows()
}

func LoadRows() {
	Normalize()
}

func ReduceRows() int {
	return 2
}

func Finalize(total int) int {
	WriteLog()
	return total
}

func WriteResponse(result int) {
	WriteLog()
}

func WriteLog() {
}

func UnrelatedHelper() int {
	return 42
}
