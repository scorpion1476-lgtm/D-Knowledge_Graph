Import-Module Pester

class Shape {
    [double] Area() {
        return 0.0
    }
}

class Circle : Shape {
    [double] Area() {
        return 3.14159
    }
}

function New-Circle {
    param($Radius)
    return [Circle]::new()
}

function Get-TotalArea {
    param($Shapes)
    New-Circle 1.0
}
