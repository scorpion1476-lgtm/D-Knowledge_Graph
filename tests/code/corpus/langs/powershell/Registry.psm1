Import-Module Shapes

function Add-Item {
    param($Item)
    Write-Output $Item
}

function Initialize-Registry {
    Add-Item 1
}
