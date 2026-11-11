Add-Type -AssemblyName System.Drawing
$bitmap = New-Object System.Drawing.Bitmap 64,64
$canvas = [System.Drawing.Graphics]::FromImage($bitmap)
$canvas.SmoothingMode = 'AntiAlias'
$canvas.Clear([System.Drawing.Color]::Transparent)
$blue = New-Object System.Drawing.SolidBrush ([System.Drawing.ColorTranslator]::FromHtml('#3568E9'))
$light = New-Object System.Drawing.SolidBrush ([System.Drawing.ColorTranslator]::FromHtml('#5484FF'))
$white = [System.Drawing.Brushes]::White
$canvas.FillPolygon($blue, [System.Drawing.Point[]]@([System.Drawing.Point]::new(6,17),[System.Drawing.Point]::new(48,4),[System.Drawing.Point]::new(48,45),[System.Drawing.Point]::new(6,59)))
$canvas.FillPolygon($light, [System.Drawing.Point[]]@([System.Drawing.Point]::new(20,32),[System.Drawing.Point]::new(57,20),[System.Drawing.Point]::new(57,53),[System.Drawing.Point]::new(20,62)))
foreach ($pos in @(@(13,20),@(13,33),@(13,46),@(27,15),@(40,11))) { $canvas.FillRectangle($white,$pos[0],$pos[1],5,6) }
$icon = [System.Drawing.Icon]::FromHandle($bitmap.GetHicon())
$stream = [System.IO.File]::Create('D:\Shiying\app\static\brand.ico')
$icon.Save($stream)
$stream.Close()
$icon.Dispose()
$canvas.Dispose()
$bitmap.Dispose()
$blue.Dispose()
$light.Dispose()
