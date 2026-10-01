using System;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Text;

internal static class QQScopeLauncher
{
    private const string Magic = "QQSCOPEP";

    private static int Main(string[] args)
    {
        
        try { return Run(args); }
        catch (Exception ex)
        {
            Console.WriteLine();
            Console.WriteLine("[错误] " + ex.Message);
            Console.WriteLine(ex.ToString());
            Console.WriteLine();
            Console.WriteLine("按任意键退出...");
            try { Console.ReadKey(true); } catch { }
            return 1;
        }
    }

    private static int Run(string[] args)
    {
        string exePath = Process.GetCurrentProcess().MainModule.FileName;
        long totalLen = new FileInfo(exePath).Length;
        long payloadOffset;
        long payloadLen;

        using (FileStream fs = File.OpenRead(exePath))
        {
            if (totalLen < 16) throw new Exception("文件不完整。");
            fs.Seek(totalLen - 16, SeekOrigin.Begin);
            byte[] trailer = new byte[16];
            ReadExactly(fs, trailer, 0, 16);
            string magic = Encoding.ASCII.GetString(trailer, 0, 8);
            if (magic != Magic) throw new Exception("未找到内置数据（此 exe 可能被修改或损坏）。");
            payloadLen = BitConverter.ToInt64(trailer, 8);
            payloadOffset = totalLen - 16 - payloadLen;
            if (payloadLen <= 0 || payloadOffset < 0) throw new Exception("内置数据长度无效。");
        }

        string exeDir = Path.GetDirectoryName(Path.GetFullPath(exePath));
        string targetFull = Path.GetFullPath(Path.Combine(exeDir, "QQScope_data"));
        string targetPrefix = targetFull + Path.DirectorySeparatorChar;
        string dataPrefix = Path.GetFullPath(Path.Combine(targetFull, "data")) + Path.DirectorySeparatorChar;
        string stampFile = Path.Combine(targetFull, ".payload.stamp");
        string batPath = Path.Combine(targetFull, "\u2460启动QQScope.bat");
        string pyPath = Path.Combine(targetFull, "python", "python.exe");

        Console.WriteLine("============================================");
        Console.WriteLine("  QQScope 便携版（内置环境自解压）");
        Console.WriteLine("============================================");
        Console.WriteLine("目标目录: " + targetFull);
        Console.WriteLine();

        bool skip = false;
        if (File.Exists(stampFile) && File.Exists(batPath) && File.Exists(pyPath))
        {
            try
            {
                if (File.ReadAllText(stampFile).Trim() == payloadLen.ToString()) skip = true;
            }
            catch { }
        }

        if (skip)
        {
            Console.WriteLine("[1/2] 检测到已解压，跳过解压（用户 data 目录保持原样）。");
        }
        else
        {
            Console.WriteLine("[1/2] 正在解压内置环境，请稍候...");
            Directory.CreateDirectory(targetFull);
            Stopwatch sw = Stopwatch.StartNew();
            long files = 0;
            long bytes = 0;
            using (FileStream fs = File.OpenRead(exePath))
            using (BoundedStream bs = new BoundedStream(fs, payloadOffset, payloadLen))
            using (ZipArchive zip = new ZipArchive(bs, ZipArchiveMode.Read))
            {
                foreach (ZipArchiveEntry entry in zip.Entries)
                {
                    string rel = entry.FullName.Replace('\\', '/').TrimStart('/');
                    if (rel.Length == 0) continue;
                    string dest = Path.GetFullPath(Path.Combine(targetFull, rel.Replace('/', Path.DirectorySeparatorChar)));
                    bool inTarget = string.Equals(dest, targetFull, StringComparison.OrdinalIgnoreCase)
                                    || dest.StartsWith(targetPrefix, StringComparison.OrdinalIgnoreCase);
                    if (!inTarget) throw new Exception("非法路径: " + rel);

                    bool isDir = rel.EndsWith("/") || entry.Name.Length == 0;
                    if (isDir)
                    {
                        Directory.CreateDirectory(dest);
                        continue;
                    }
                    Directory.CreateDirectory(Path.GetDirectoryName(dest));

                    if (dest.StartsWith(dataPrefix, StringComparison.OrdinalIgnoreCase) && File.Exists(dest))
                        continue;

                    using (Stream src = entry.Open())
                    using (FileStream dst = new FileStream(dest, FileMode.Create, FileAccess.Write, FileShare.None, 1048576))
                    {
                        src.CopyTo(dst, 1048576);
                    }
                    files++;
                    bytes += entry.Length;
                    if (files % 300 == 0)
                        Console.WriteLine("      ... 已解压 " + files + " 个文件 / " + (bytes / 1048576) + " MB");
                }
            }
            File.WriteAllText(stampFile, payloadLen.ToString());
            sw.Stop();
            Console.WriteLine("[1/2] 解压完成: " + files + " 个文件, " + (bytes / 1048576) + " MB, 用时 " + sw.Elapsed.TotalSeconds.ToString("F1") + " 秒");
        }

        if (!File.Exists(batPath)) throw new Exception("解压后找不到启动脚本: " + batPath);

        bool extractOnly = false;
        foreach (string a in args) { if (a == "--extract-only") extractOnly = true; }
        if (extractOnly)
        {
            Console.WriteLine("[提示] --extract-only：仅解压，不启动。");
            return 0;
        }

        Console.WriteLine("[2/2] 启动 QQScope ...");
        Console.WriteLine("      数据目录: " + Path.Combine(targetFull, "data"));
        Console.WriteLine("      关闭 QQScope 窗口即退出程序。");
        Console.WriteLine();
        ProcessStartInfo psi = new ProcessStartInfo();
        psi.FileName = batPath;
        psi.WorkingDirectory = targetFull;
        psi.UseShellExecute = true;
        Process.Start(psi);
        Console.WriteLine("已启动，本窗口 5 秒后自动关闭。");
        System.Threading.Thread.Sleep(5000);
        return 0;
    }

    private static void ReadExactly(Stream s, byte[] buf, int off, int count)
    {
        int done = 0;
        while (done < count)
        {
            int n = s.Read(buf, off + done, count - done);
            if (n <= 0) throw new EndOfStreamException();
            done += n;
        }
    }
}

internal sealed class BoundedStream : Stream
{
    private readonly Stream _base;
    private readonly long _start;
    private readonly long _length;
    private long _pos;

    public BoundedStream(Stream baseStream, long start, long length)
    {
        _base = baseStream; _start = start; _length = length; _pos = 0;
    }

    public override bool CanRead { get { return true; } }
    public override bool CanSeek { get { return true; } }
    public override bool CanWrite { get { return false; } }
    public override long Length { get { return _length; } }
    public override long Position { get { return _pos; } set { Seek(value, SeekOrigin.Begin); } }

    public override int Read(byte[] buffer, int offset, int count)
    {
        if (_pos >= _length) return 0;
        long remain = _length - _pos;
        if (count > remain) count = (int)remain;
        _base.Seek(_start + _pos, SeekOrigin.Begin);
        int n = _base.Read(buffer, offset, count);
        if (n > 0) _pos += n;
        return n;
    }

    public override long Seek(long offset, SeekOrigin origin)
    {
        long np;
        if (origin == SeekOrigin.Begin) np = offset;
        else if (origin == SeekOrigin.Current) np = _pos + offset;
        else np = _length + offset;
        if (np < 0 || np > _length) throw new IOException("seek out of range");
        _pos = np;
        return _pos;
    }

    public override void Flush() { }
    public override void SetLength(long value) { throw new NotSupportedException(); }
    public override void Write(byte[] buffer, int offset, int count) { throw new NotSupportedException(); }
}