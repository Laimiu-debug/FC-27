param([Parameter(Mandatory=$true)][string]$KeyFile)
$ErrorActionPreference = 'Stop'
$taskKeyData = $null
$taskAes = $null
$taskCipherStream = [IO.MemoryStream]::new()
try {
    $taskKeyData = [IO.File]::ReadAllBytes($KeyFile)
    if ($taskKeyData.Length -lt 16 -or $taskKeyData.Length -gt 32768) { throw 'Invalid format-key length' }
    [Console]::OpenStandardInput().CopyTo($taskCipherStream)
    $taskCipher = $taskCipherStream.ToArray()
    if ($taskCipher.Length -eq 0 -or $taskCipher.Length -gt 33554432 -or $taskCipher.Length % 16 -ne 0) { throw 'Invalid cipher length' }
    $taskAes = [Security.Cryptography.Aes]::Create()
    $taskAes.Mode = [Security.Cryptography.CipherMode]::CBC
    $taskAes.Padding = [Security.Cryptography.PaddingMode]::PKCS7
    $taskAes.Key = $taskKeyData[0..15]
    $taskAes.IV = $taskKeyData[0..15]
    $taskDecryptor = $taskAes.CreateDecryptor()
    try { $taskPlain = $taskDecryptor.TransformFinalBlock($taskCipher,0,$taskCipher.Length) } finally { $taskDecryptor.Dispose() }
    $taskOutputStream = [Console]::OpenStandardOutput()
    $taskOutputStream.Write($taskPlain,0,$taskPlain.Length)
    $taskOutputStream.Flush()
} catch {
    [Console]::Error.WriteLine('Initfs AES decoding failed')
    exit 1
} finally {
    if ($null -ne $taskAes) { $taskAes.Dispose() }
    if ($null -ne $taskKeyData) { [Array]::Clear($taskKeyData,0,$taskKeyData.Length) }
    $taskCipherStream.Dispose()
}
