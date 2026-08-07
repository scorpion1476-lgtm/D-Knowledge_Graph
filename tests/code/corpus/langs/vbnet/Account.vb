Imports System
Imports System.Collections.Generic

Public Class Account
    Inherits BaseAccount

    Private balance As Double

    Public Function Balance() As Double
        Return Compute()
    End Function

    Private Function Compute() As Double
        Return balance
    End Function

    Public Sub Deposit(amount As Double)
        balance = balance + amount
    End Sub
End Class

Public Interface IShape
End Interface

Public Structure Point
End Structure

Public Enum Kind
End Enum
