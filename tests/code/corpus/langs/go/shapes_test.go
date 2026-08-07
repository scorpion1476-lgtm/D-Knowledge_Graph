package geometry

import "testing"

func TestMakeCircle(t *testing.T) {
	if MakeCircle(1).Area() == 0 {
		t.Fatal("area should not be zero")
	}
}

func TestTotalArea(t *testing.T) {
	shapes := buildShapes()
	if TotalArea(shapes) == 0 {
		t.Fatal("total should not be zero")
	}
}

func buildShapes() []Shape {
	return []Shape{MakeCircle(1)}
}
