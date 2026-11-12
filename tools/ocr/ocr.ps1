param([string]$Dir, [string]$Out)
# OCR (Windows.Media.Ocr, pt-BR) de cada PNG da pasta -> JSON por página com palavras e caixas (pixels)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) {
    $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result
}
$null = [Windows.Globalization.Language, Windows.Globalization, ContentType = WindowsRuntime]
$lang = [Windows.Globalization.Language]::new("pt-BR")
$eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($lang)
New-Item -ItemType Directory -Force $Out | Out-Null
Get-ChildItem $Dir -Filter *.png | Sort-Object Name | ForEach-Object {
    $dest = Join-Path $Out ($_.BaseName + '.json')
    if (Test-Path $dest) { return }
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($_.FullName)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bmp = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $res = Await ($eng.RecognizeAsync($bmp)) ([Windows.Media.Ocr.OcrResult])
    $linhas = @()
    foreach ($l in $res.Lines) {
        $ws = @()
        foreach ($w in $l.Words) { $r = $w.BoundingRect; $ws += ,@($w.Text, [math]::Round($r.X), [math]::Round($r.Y), [math]::Round($r.X + $r.Width), [math]::Round($r.Y + $r.Height)) }
        $linhas += ,$ws
    }
    $stream.Dispose()
    ConvertTo-Json -Compress -Depth 5 @{ w = $bmp.PixelWidth; h = $bmp.PixelHeight; linhas = $linhas } | Out-File -Encoding utf8 $dest
}
"fim"
