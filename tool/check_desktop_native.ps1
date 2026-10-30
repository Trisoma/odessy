# Windows PowerShell 5.1: native DEMO acceptance, without global keyboard input.
param(
  [Parameter(Mandatory=$true)][string]$BundlePath,
  [Parameter(Mandatory=$true)][string]$ReportPath,
  [switch]$WindowLayout
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName Accessibility
Add-Type -ReferencedAssemblies Accessibility @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;

public sealed class DesktopNativeNode {
  public string Name;
  public string Value;
  public int Role;
  public int State;
  public int Left;
  public int Top;
  public int Width;
  public int Height;
}

public sealed class DesktopNativeViewport {
  public uint Dpi;
  public int ClientWidth;
  public int ClientHeight;
  public int Left;
  public int Top;
}

public static class DesktopNativeAcceptance {
  public delegate bool Visitor(IntPtr handle, IntPtr parameter);
  public static int Owner;
  public static int LookupRetries;
  public static bool WindowLayout;
  [StructLayout(LayoutKind.Sequential)] struct Rect { public int Left, Top, Right, Bottom; }
  [StructLayout(LayoutKind.Sequential)] struct Point { public int X, Y; }
  [DllImport("user32.dll")] static extern uint GetDpiForWindow(IntPtr handle);
  [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr handle, out Rect rect);
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr handle, out Rect rect);
  [DllImport("user32.dll")] static extern bool ClientToScreen(IntPtr handle, ref Point point);
  [DllImport("user32.dll")] static extern bool SetWindowPos(IntPtr handle, IntPtr after, int x, int y, int width, int height, uint flags);
  [DllImport("user32.dll")] public static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
  [DllImport("user32.dll")] static extern bool EnumWindows(Visitor visitor, IntPtr parameter);
  [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr parent, Visitor visitor, IntPtr parameter);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr handle, out uint owner);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr handle, StringBuilder name, int count);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern bool PostMessage(IntPtr handle, uint message, IntPtr wParam, IntPtr lParam);
  [DllImport("user32.dll")] static extern IntPtr SendMessageTimeout(IntPtr handle, uint message, IntPtr wParam, IntPtr lParam, uint flags, uint timeout, out IntPtr result);
  [DllImport("oleacc.dll")] static extern int AccessibleObjectFromWindow(IntPtr handle, uint objectId, ref Guid iid, [MarshalAs(UnmanagedType.Interface)] out object result);

  static void VerifyOwner(IntPtr handle) {
    uint owner;
    GetWindowThreadProcessId(handle, out owner);
    if (handle == IntPtr.Zero || owner != Owner) throw new InvalidOperationException("Window is not owned by this desktop process.");
  }
  public static IntPtr FindWindow() {
    IntPtr found = IntPtr.Zero;
    EnumWindows(delegate(IntPtr handle, IntPtr parameter) {
      uint owner; GetWindowThreadProcessId(handle, out owner);
      var name = new StringBuilder(256); GetClassName(handle, name, name.Capacity);
      if (owner == Owner && name.ToString() == "FLUTTER_RUNNER_WIN32_WINDOW") { found=handle; return false; }
      return true;
    }, IntPtr.Zero);
    return found;
  }
  public static IntPtr FindView(IntPtr parent) {
    VerifyOwner(parent);
    IntPtr found = IntPtr.Zero;
    EnumChildWindows(parent, delegate(IntPtr handle, IntPtr parameter) {
      uint owner; GetWindowThreadProcessId(handle, out owner);
      var name = new StringBuilder(256); GetClassName(handle, name, name.Capacity);
      if (owner == Owner && name.ToString() == "FLUTTERVIEW") { found=handle; return false; }
      return true;
    }, IntPtr.Zero);
    return found;
  }
  public static void RequestSemantics(IntPtr view) {
    VerifyOwner(view);
    IntPtr result;
    if (SendMessageTimeout(view, 0x3d, IntPtr.Zero, new IntPtr(-4), 2, 2000, out result) == IntPtr.Zero)
      throw new InvalidOperationException("MSAA request timed out.");
  }
  static Accessibility.IAccessible Root(IntPtr view) {
    VerifyOwner(view);
    object accessible;
    var iid = new Guid("618736E0-3C3D-11CF-810C-00AA00389B71");
    int result = AccessibleObjectFromWindow(view, unchecked((uint)-4), ref iid, out accessible);
    if (result != 0) Marshal.ThrowExceptionForHR(result);
    var root = accessible as Accessibility.IAccessible;
    if (root == null) throw new InvalidOperationException("Missing MSAA root.");
    return root;
  }
  static void Walk(Accessibility.IAccessible node, List<Accessibility.IAccessible> nodes, int depth) {
    if (depth > 64 || nodes.Count > 5000) throw new InvalidOperationException("Unexpected accessibility tree size.");
    nodes.Add(node);
    int count = node.accChildCount;
    for (int i=1; i<=count; i++) {
      var child = node.get_accChild(i) as Accessibility.IAccessible;
      if (child == null) throw new InvalidOperationException("Unexpected simple MSAA child.");
      Walk(child, nodes, depth+1);
    }
  }
  static List<Accessibility.IAccessible> Nodes(IntPtr view) {
    var nodes = new List<Accessibility.IAccessible>();
    Walk(Root(view), nodes, 0);
    return nodes;
  }
  static bool verifiedDemo;
  static void RequireDemo(List<Accessibility.IAccessible> nodes, bool allowDiscardChoice=false) {
    bool badge=false, disclaimer=false, discardPrompt=false, discardDetail=false;
    bool navigationButton=false, drawerTitle=false, brand=false;
    foreach(var node in nodes) {
      var name=node.get_accName(0);
      badge |= name == "DEMO";
      disclaimer |= name == "Demo \u00b7 no tools execute" ||
        name == "Demo \u00b7 no tools execute. Demo \u00b7 no tools execute";
      discardPrompt |= name == "Discard unsaved changes?";
      discardDetail |= name == "Your collaboration limit has not been saved.";
      navigationButton |= name == "Open navigation menu";
      drawerTitle |= name == "Navigation menu";
      brand |= name == "Odessy";
    }
    if (badge && disclaimer) { verifiedDemo=true; return; }
    // Flutter hides background semantics while a modal is open. Only the two
    // choices of this exact draft dialog are allowed in a verified DEMO process.
    if (verifiedDemo && allowDiscardChoice && discardPrompt && discardDetail) return;
    // The compact shell hides its footer when closed, and the open drawer hides
    // background semantics. This exception is limited to the layout test profile
    // in a process already verified as DEMO; live-connection actions stay absent.
    if (WindowLayout && verifiedDemo && ((badge && navigationButton) || (drawerTitle && brand))) return;
    throw new InvalidOperationException("Refusing interaction outside labelled DEMO mode.");
  }
  public static DesktopNativeNode[] Snapshot(IntPtr view) {
    var result = new List<DesktopNativeNode>();
    foreach (var node in Nodes(view)) {
      var snapshot = new DesktopNativeNode {
        Name=node.get_accName(0) ?? "", Value=node.get_accValue(0) ?? "",
        Role=Convert.ToInt32(node.get_accRole(0)), State=Convert.ToInt32(node.get_accState(0))
      };
      if (WindowLayout) node.accLocation(out snapshot.Left, out snapshot.Top, out snapshot.Width, out snapshot.Height, 0);
      result.Add(snapshot);
    }
    return result.ToArray();
  }
  static readonly HashSet<string> AllowedButtons = new HashSet<string> {
    "Jetson", "Laptop host", "Settings", "Overview", "Conversation", "General",
    "Models and connections", "Voice", "Devices and tool access", "Advanced", "Installation",
    "Use light theme", "Use dark theme", "Save settings", "Reload", "Cancel",
    "Send message", "Deny", "Approve once", "Answer", "Stop current turn",
    "Open navigation menu", "Dismiss", "History", "Discard changes"
  };
  static Accessibility.IAccessible Resolve(IntPtr view, string name, int role, int occurrence, bool modalCancel) {
    for(int attempt=0; ; attempt++) {
      try {
        var nodes=Nodes(view); RequireDemo(nodes, modalCancel);
        int index=0;
        foreach(var node in nodes) if((node.get_accName(0)==name || ((name=="Jetson" || name=="Laptop host") && (node.get_accName(0)??"").StartsWith(name+"\n"))) && Convert.ToInt32(node.get_accRole(0))==role)
          if(index++==occurrence) return node;
        throw new InvalidOperationException("Control not found: "+name);
      } catch(Exception e) {
        if(attempt==29 || (e.HResult!=-2147024809 && e.HResult!=-2147467259)) throw;
        LookupRetries++; Thread.Sleep(50);
      }
    }
  }
  public static void Invoke(IntPtr view, string name, int occurrence) {
    if (!AllowedButtons.Contains(name)) throw new InvalidOperationException("Button outside acceptance allowlist.");
    var target=Resolve(view,name,43,occurrence,name=="Cancel" || name=="Discard changes");
    // Retry tree lookups only; never replay a dispatched action.
    target.accDoDefaultAction(0);
  }
  static void Post(IntPtr view, uint message, long key, long data) {
    VerifyOwner(view);
    if (!PostMessage(view, message, new IntPtr(key), new IntPtr(data)))
      throw new InvalidOperationException("Input message failed.");
  }
  public static void Type(IntPtr view, string label, string value) {
    if (label!="Consultation limit" && label!="Message. Desktop voice is unavailable" && label!="Message" && label!="Your answer")
      throw new InvalidOperationException("Field outside acceptance allowlist.");
    var field=Resolve(view,label,42,0,false);
    int length=(field.get_accValue(0)??"").Length;
    if(length>256 || value.Length>256) throw new InvalidOperationException("Unexpected field length.");
    // MSAA set_accValue is not exposed by this engine. Focus through the semantic
    // action, then send input only to the verified Flutter child HWND.
    field.accDoDefaultAction(0);
    Thread.Sleep(150);
    Post(view,0x100,35,1); Post(view,0x101,35,0xC0000001L); // End
    for(int i=0;i<length;i++) {Post(view,0x100,8,0x000E0001);Post(view,0x101,8,0xC00E0001L);}
    foreach(char c in value) Post(view,0x102,c,1); // WM_CHAR, including UTF-16
  }
  public static void Close(IntPtr parent) { VerifyOwner(parent); Post(parent,0x10,0,0); }
  public static DesktopNativeViewport Viewport(IntPtr parent) {
    VerifyOwner(parent);
    Rect client; var origin=new Point(); uint dpi=GetDpiForWindow(parent);
    if(dpi==0 || !GetClientRect(parent,out client) || !ClientToScreen(parent,ref origin))
      throw new InvalidOperationException("Could not read owned viewport.");
    return new DesktopNativeViewport {Dpi=dpi,ClientWidth=client.Right,ClientHeight=client.Bottom,Left=origin.X,Top=origin.Y};
  }
  public static DesktopNativeViewport Resize(IntPtr parent, int logicalWidth, int logicalHeight) {
    VerifyOwner(parent);
    if(logicalWidth<320 || logicalWidth>1600 || logicalHeight<320 || logicalHeight>1200)
      throw new InvalidOperationException("Viewport outside acceptance bounds.");
    var current=Viewport(parent); Rect outer;
    if(!GetWindowRect(parent,out outer)) throw new InvalidOperationException("Could not read owned window bounds.");
    int width=(int)Math.Round(logicalWidth*current.Dpi/96.0);
    int height=(int)Math.Round(logicalHeight*current.Dpi/96.0);
    if(!SetWindowPos(parent,IntPtr.Zero,0,0,width+outer.Right-outer.Left-current.ClientWidth,height+outer.Bottom-outer.Top-current.ClientHeight,0x16))
      throw new InvalidOperationException("Could not resize owned window.");
    return Viewport(parent);
  }
  public static void Enter(IntPtr view, string label, bool shift) {
    var field=Resolve(view,label,42,0,false);
    if((Convert.ToInt32(field.get_accState(0)) & 4)==0)
      throw new InvalidOperationException("Composer does not own focus.");
    if(shift) Post(view,0x100,16,0x002A0001);
    Post(view,0x100,13,0x001C0001); Post(view,0x102,13,0x001C0001); Post(view,0x101,13,0xC01C0001L);
    if(shift) Post(view,0x101,16,0xC02A0001L);
  }
  public static void EscapeDrawer(IntPtr view) {
    if(!WindowLayout) throw new InvalidOperationException("Drawer key outside layout profile.");
    var nodes=Nodes(view); RequireDemo(nodes);
    bool drawer=false;
    foreach(var node in nodes) drawer |= node.get_accName(0)=="Navigation menu";
    if(!drawer) throw new InvalidOperationException("No owned navigation drawer is open.");
    Post(view,0x100,27,0x00010001); Post(view,0x102,27,0x00010001); Post(view,0x101,27,0xC0010001L);
  }
}
'@

$bundle = (Resolve-Path -LiteralPath $BundlePath).Path
$exe = Join-Path $bundle 'odessy_desktop.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'Desktop EXE is missing.' }
$reportFile = [IO.Path]::GetFullPath($ReportPath)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $reportFile) | Out-Null
$checks = New-Object 'System.Collections.Generic.List[object]'
$viewports = New-Object 'System.Collections.Generic.List[object]'
$findings = New-Object 'System.Collections.Generic.List[object]'
$unqualified = New-Object 'System.Collections.Generic.List[object]'
$report = [ordered]@{
  date=(Get-Date).ToUniversalTime().ToString('o'); bundle=$bundle;
  exe_sha256=(Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash;
  mode='hidden-window DEMO'; interface='MSAA + HWND-targeted input';
  profile=$(if($WindowLayout){'window layout and targeted keys'}else{'interaction'});
  passed=$false; closed_cleanly=$false; checks=$checks; errors=@(); tree_retry_count=0;
  viewports=$viewports; findings=$findings; unqualified=$unqualified;
  limitations=@('Not a visible rendering test','Not physical keyboard/IME input','Not a screen-reader session','Not live monitoring/provider qualification')
}
$nativeProcess=$null; $window=[IntPtr]::Zero; $view=[IntPtr]::Zero
$previousDpiContext=[IntPtr]::Zero
$layoutTraversalCompleted=$false
$demoDisclaimer='Demo ' + [char]0x00b7 + ' no tools execute'
$unicodeMessage='Native smoke ' + [char]0x2014 + ' ' + [char]0x4e2d + [char]0x6587 + ' ' + [char]0x2713

function Read-Nodes {
  for($attempt=0; $attempt -lt 30; $attempt++) {
    try { return @([DesktopNativeAcceptance]::Snapshot($view)) }
    catch {
      # Flutter can replace a child between accChildCount and get_accChild.
      # E_INVALIDARG/E_FAIL occur while replaced nodes are being detached. A
      # persistent error still fails the run; every retry is counted in evidence.
      if($_.Exception.GetBaseException().HResult -notin @(-2147024809,-2147467259) -or $attempt -eq 29) { throw }
      $report.tree_retry_count++
      Start-Sleep -Milliseconds 50
    }
  }
}
function Has-Text($nodes, [string]$text) {
  @($nodes | Where-Object { $_.Name.Contains($text) -or $_.Value.Contains($text) }).Count -gt 0
}
function Has-Value($nodes, [string]$label, [string]$value) {
  @($nodes | Where-Object { $_.Role -eq 42 -and $_.Name -eq $label -and $_.Value -eq $value }).Count -eq 1
}
function Selected($nodes, [string]$label) {
  @($nodes | Where-Object { $_.Role -eq 43 -and ($_.Name -eq $label -or $_.Name.StartsWith($label+"`n")) -and ($_.State -band 2) -ne 0 }).Count -eq 1
}
function Expect([string]$name, [scriptblock]$predicate) {
  $deadline=[DateTime]::UtcNow.AddSeconds(8)
  do {
    if ($nativeProcess.HasExited) { throw "Desktop exited during: $name" }
    $nodes=Read-Nodes
    if (& $predicate $nodes) {
      $checks.Add([ordered]@{name=$name;passed=$true;node_count=$nodes.Count})
      Write-Output "PASS $name"
      return
    }
    Start-Sleep -Milliseconds 100
  } while ([DateTime]::UtcNow -lt $deadline)
  $checks.Add([ordered]@{name=$name;passed=$false;nodes=$nodes})
  throw "Native assertion timed out: $name"
}
function Click([string]$label, [int]$occurrence=0) {
  # New semantics can precede completion of Flutter's transition frame. Resolve
  # the current node after settling; dispatch once and never retry an action.
  Start-Sleep -Milliseconds 250
  [DesktopNativeAcceptance]::Invoke($view,$label,$occurrence)
}
function Input-Field([string]$label,[string]$value) {
  [DesktopNativeAcceptance]::Type($view,$label,$value)
  $capturedLabel=$label; $capturedValue=$value
  Expect "Field value: $label = $value" { param($nodes) Has-Value $nodes $capturedLabel $capturedValue }
}
function Send-Demo([string]$message) {
  Input-Field 'Message. Desktop voice is unavailable' $message
  Click 'Send message'
}
function In-Viewport([string]$label, [int]$role=43) {
  # Drawer semantics appear before its opening animation finishes. Measure settled
  # bounds rather than reporting a transitional negative X as a layout defect.
  $deadline=[DateTime]::UtcNow.AddSeconds(2)
  do {
    $nodes=Read-Nodes
    $frame=[DesktopNativeAcceptance]::Viewport($window)
    $targets=@($nodes | Where-Object { $_.Name -eq $label -and $_.Role -eq $role })
    $inside=@($targets | Where-Object {
      $_.Width -gt 0 -and $_.Height -gt 0 -and $_.Left -ge $frame.Left-1 -and $_.Top -ge $frame.Top-1 -and
      $_.Left+$_.Width -le $frame.Left+$frame.ClientWidth+1 -and $_.Top+$_.Height -le $frame.Top+$frame.ClientHeight+1
    }).Count -gt 0
    if($inside) { break }
    Start-Sleep -Milliseconds 100
  } while([DateTime]::UtcNow -lt $deadline)
  $checks.Add([ordered]@{name="Control within client: $label";passed=$inside;targets=$targets;viewport=$frame})
  if(-not $inside) {
    $findings.Add([ordered]@{control=$label;viewport=$frame;targets=$targets;issue='Control missing or outside owned client bounds';owner='App shell/foundation review required'})
    Write-Output "FINDING $label is missing or outside client bounds"
  } else { Write-Output "PASS Control within client: $label" }
}
function Close-Drawer {
  if(Has-Text (Read-Nodes) 'Navigation menu') {
    [DesktopNativeAcceptance]::EscapeDrawer($view)
    Expect 'Targeted Escape closes navigation drawer' {param($n) -not (Has-Text $n 'Navigation menu')}
  }
}
function Invoke-WindowLayout {
  [DesktopNativeAcceptance]::WindowLayout=$true
  $script:previousDpiContext=[DesktopNativeAcceptance]::SetThreadDpiAwarenessContext([IntPtr](-4))
  if($previousDpiContext -eq [IntPtr]::Zero) { throw 'Could not enter DPI-aware measurement scope.' }
  foreach($size in @(@(1200,850),@(900,720),@(899,720),@(720,800),@(480,720),@(360,640),@(1200,480))) {
    $width=$size[0]; $height=$size[1]
    $frame=[DesktopNativeAcceptance]::Resize($window,$width,$height)
    $viewports.Add([ordered]@{requested_width=$width;requested_height=$height;actual=$frame})
    if([Math]::Abs($frame.ClientWidth*96.0/$frame.Dpi-$width) -gt 1 -or [Math]::Abs($frame.ClientHeight*96.0/$frame.Dpi-$height) -gt 1) {
      throw 'Requested logical viewport was not achieved.'
    }
    $compact=$width -lt 900
    Expect "Shell breakpoint at $width x $height" {param($n) (Has-Text $n 'Open navigation menu') -eq $compact }
    if($compact) {
      Click 'Open navigation menu'
      Expect "Drawer opens at $width x $height" {param($n) Has-Text $n 'Navigation menu'}
    }
    $findingCount=$findings.Count
    In-Viewport 'Settings'
    # Do not activate a clipped control just because MSAA can still enumerate it.
    # Retain the finding and continue independent sizes/keyboard checks.
    if($findings.Count -gt $findingCount) { continue }
    Click 'Settings'
    if($compact) { Close-Drawer }
    Expect "Settings navigation at $width x $height" {param($n) Has-Text $n 'Global settings'}
    In-Viewport 'Global settings' 20
    if($compact) {
      Click 'Open navigation menu'
      Expect "Drawer reopens at $width x $height" {param($n) Has-Text $n 'Navigation menu'}
    }
    Click 'Conversation'
    if($compact) { Close-Drawer }
    Expect "Conversation returns at $width x $height" {param($n) @($n | Where-Object {$_.Role -eq 42 -and $_.Name.StartsWith('Message')}).Count -eq 1}
  }
  $script:layoutTraversalCompleted=$true
  [void][DesktopNativeAcceptance]::Resize($window,1200,850)
  Expect 'Wide layout restored before keyboard checks' {param($n) (Has-Text $n 'Demo') -and -not (Has-Text $n 'Open navigation menu')}
  Input-Field 'Message. Desktop voice is unavailable' 'Native Enter key'
  [DesktopNativeAcceptance]::Enter($view,'Message. Desktop voice is unavailable',$false)
  Expect 'Targeted Enter sends draft' {param($n) (Has-Text $n "You`nNative Enter key") -and (Has-Value $n 'Message. Desktop voice is unavailable' '') -and (Has-Text $n 'Ready')}
  Input-Field 'Message. Desktop voice is unavailable' 'Native line one'
  [DesktopNativeAcceptance]::Enter($view,'Message. Desktop voice is unavailable',$true)
  try {
    Expect 'Targeted Shift+Enter inserts newline' {param($n) Has-Value $n 'Message. Desktop voice is unavailable' "Native line one`n"}
  } catch {
    if($_.Exception.Message -ne 'Native assertion timed out: Targeted Shift+Enter inserts newline') { throw }
    # PostMessage does not change the target thread's real modifier-key state.
    # Retain the observed nodes, but do not classify this injection limitation
    # as either a passed shortcut or a demonstrated product defect.
    $checks[$checks.Count-1]['passed']=$null
    $checks[$checks.Count-1]['status']='not-qualified'
    $checks[$checks.Count-1]['reason']='Posted Shift messages cannot establish real modifier-key state; physical/foreground acceptance is required.'
    $unqualified.Add([ordered]@{name='Shift+Enter';reason='HWND message injection did not establish Shift state';observed='Draft was submitted rather than receiving a newline';required='Physical/foreground keyboard acceptance'})
    Write-Output 'NOT QUALIFIED Shift+Enter: modifier injection is not physical input'
  }
}

try {
  $nativeProcess=Start-Process -FilePath $exe -WorkingDirectory $bundle -WindowStyle Hidden -PassThru
  [DesktopNativeAcceptance]::Owner=$nativeProcess.Id
  $report.process_id=$nativeProcess.Id
  $deadline=[DateTime]::UtcNow.AddSeconds(15)
  do {
    if ($nativeProcess.HasExited) { throw 'Desktop exited before creating its Flutter view.' }
    $window=[DesktopNativeAcceptance]::FindWindow()
    if($window -ne [IntPtr]::Zero) { $view=[DesktopNativeAcceptance]::FindView($window) }
    if($view -ne [IntPtr]::Zero) { break }
    Start-Sleep -Milliseconds 100
  } while([DateTime]::UtcNow -lt $deadline)
  if($view -eq [IntPtr]::Zero) { throw 'Flutter child window did not appear.' }
  [DesktopNativeAcceptance]::RequestSemantics($view)
  Expect 'Labelled DEMO conversation is ready' { param($n) (Has-Text $n 'DEMO') -and (Has-Text $n $demoDisclaimer) -and (Has-Text $n 'Ready') }
  if($WindowLayout) {
    Invoke-WindowLayout
  } else {
  Click 'Jetson'
  Expect 'Device overview displays labelled demo telemetry' { param($n) (Selected $n 'Jetson') -and (Has-Text $n 'Telemetry access') -and (Has-Text $n 'Allowed') -and (Has-Text $n '17.5%') }
  Click 'History'
  Expect 'History is composed into the device page' { param($n) (Has-Text $n 'Monitoring history') -and (Has-Text $n '60 seconds per bucket') }
  Click 'Overview'
  Expect 'Overview returns to supplied monitoring report' { param($n) Has-Text $n '17.5%' }
  Click 'Settings' 1
  Expect 'Device-specific settings remain unavailable' { param($n) Has-Text $n 'Device configuration cannot be changed here yet.' }
  Click 'Laptop host'
  Expect 'Switching device returns to Overview' { param($n) (Selected $n 'Laptop host') -and (Has-Text $n '17.5%') -and -not (Has-Text $n 'Device configuration cannot be changed here yet.') }

  Click 'Settings'
  Expect 'Global settings opens General' { param($n) (Has-Text $n 'Global settings') -and (Selected $n 'General') }
  Click 'Use light theme'
  Expect 'Light theme state is reflected' { param($n) Has-Text $n 'Use dark theme' }
  Click 'Use dark theme'
  Expect 'Dark theme state is reflected' { param($n) Has-Text $n 'Use light theme' }
  foreach($category in @('Voice','Devices and tool access','Advanced','Installation','Models and connections')) {
    Click $category
    $capturedCategory=$category
    Expect "Settings category: $category" { param($n) Selected $n $capturedCategory }
  }
  Expect 'Existing collaboration limit is loaded' { param($n) Has-Value $n 'Consultation limit' '1' }
  Input-Field 'Consultation limit' '33'
  Expect 'Out-of-range settings show validation' { param($n) Has-Text $n 'Enter a whole number from 0 to 32.' }
  Input-Field 'Consultation limit' '4'
  Click 'General'
  Expect 'Category switch preserves dirty reminder' { param($n) (Selected $n 'General') -and (Has-Text $n 'Unsaved collaboration changes') }
  Click 'Models and connections'
  Expect 'Category return preserves draft' { param($n) Has-Value $n 'Consultation limit' '4' }
  Click 'Conversation'
  Expect 'Outer navigation asks before discarding draft' { param($n) Has-Text $n 'Discard unsaved changes?' }
  Click 'Cancel'
  Expect 'Cancel outer navigation preserves draft' { param($n) Has-Value $n 'Consultation limit' '4' }
  Click 'Laptop host'
  Expect 'Device inspection asks before discarding draft' { param($n) Has-Text $n 'Discard unsaved changes?' }
  Click 'Cancel'
  Expect 'Cancel device inspection preserves draft' { param($n) Has-Value $n 'Consultation limit' '4' }
  [DesktopNativeAcceptance]::Close($window)
  Expect 'Native close asks before discarding draft' { param($n) Has-Text $n 'Discard unsaved changes?' }
  Click 'Cancel'
  Expect 'Cancel native close preserves live DEMO draft' { param($n) (Has-Value $n 'Consultation limit' '4') -and (Has-Text $n 'DEMO') }
  Click 'Reload'
  Expect 'Reload asks before discarding draft' { param($n) Has-Text $n 'Discard unsaved changes?' }
  Click 'Cancel'
  Expect 'Cancel preserves settings draft' { param($n) (Has-Value $n 'Consultation limit' '4') -and -not (Has-Text $n 'Discard unsaved changes?') }
  Click 'Save settings'
  Expect 'Save is explicitly demo memory only' { param($n) Has-Text $n 'Saved in demo memory only.' }
  Click 'Reload'
  Expect 'Reload retains saved demo value' { param($n) (Has-Value $n 'Consultation limit' '4') -and -not (Has-Text $n 'Unsaved collaboration changes') }

  Input-Field 'Consultation limit' '5'
  Click 'Conversation'
  Expect 'Dirty saved form asks before departure' { param($n) Has-Text $n 'Discard unsaved changes?' }
  Click 'Discard changes'
  Expect 'Explicit discard leaves settings without saving' { param($n) (Has-Text $n 'Ready') -and -not (Has-Text $n 'Global settings') }
  Click 'Settings'
  Click 'Models and connections'
  Expect 'Discard did not overwrite the saved value' { param($n) Has-Value $n 'Consultation limit' '4' }
  Click 'Conversation'
  Expect 'Device inspection preserved coordinator' { param($n) Has-Text $n 'Coordinator: Demo workstation' }
  Send-Demo $unicodeMessage
  Expect 'Unicode draft sends and demo responds' { param($n) (Has-Text $n $unicodeMessage) -and (Has-Text $n 'This is a demonstration response. No model or tool ran.') -and (Has-Value $n 'Message. Desktop voice is unavailable' '') }
  Send-Demo '/approval'
  Expect 'Approval offers exact command and both responses' { param($n) (Has-Text $n 'git status --short') -and (Has-Text $n 'Approve once') -and (Has-Text $n 'Deny') }
  Click 'Deny'
  Expect 'Denial resolves the demo request' { param($n) Has-Text $n 'Demo action denied. Nothing was executed.' }
  Send-Demo '/approval'
  Expect 'Second demo approval is pending' { param($n) Has-Text $n 'Approve once' }
  Click 'Approve once'
  Expect 'Approval resolves without executing tools' { param($n) Has-Text $n 'Demo action approved. Nothing was executed.' }
  Send-Demo '/question'
  Expect 'Question remains information-only' { param($n) (Has-Text $n 'Your answer') -and (Has-Text $n 'It does not grant permission.') }
  Input-Field 'Your answer' 'native smoke repository'
  Click 'Answer'
  Expect 'Question response reaches demo session' { param($n) Has-Text $n 'Demo answer received: native smoke repository' }
  Send-Demo 'Native cancellation check'
  Expect 'Running turn exposes Stop' { param($n) Has-Text $n 'Stop current turn' }
  Click 'Stop current turn'
  Expect 'Stop ends turn without promising rollback' { param($n) (Has-Text $n 'Stopped') -and (Has-Text $n 'completed actions remain completed') -and (Has-Text $n 'Send message') -and -not (Has-Text $n 'Stop current turn') }
  }
  $report.passed=($findings.Count -eq 0 -and $unqualified.Count -eq 0)
} catch {
  $report.errors += $_.Exception.Message
  if($view -ne [IntPtr]::Zero) {
    try { $report.last_nodes=Read-Nodes } catch { $report.errors += 'Could not capture final owned accessibility tree.' }
  }
} finally {
  if($previousDpiContext -ne [IntPtr]::Zero) { [void][DesktopNativeAcceptance]::SetThreadDpiAwarenessContext($previousDpiContext) }
  if($nativeProcess -and -not $nativeProcess.HasExited) {
    if($window -ne [IntPtr]::Zero) { [DesktopNativeAcceptance]::Close($window) }
    if(-not $nativeProcess.WaitForExit(10000)) {
      $report.errors += 'Owned desktop did not close in 10 seconds.'
      $nativeProcess.Kill(); $nativeProcess.WaitForExit()
    }
  }
  if($nativeProcess -and $nativeProcess.HasExited) {
    $report.exit_code=$nativeProcess.ExitCode
    $report.closed_cleanly=($nativeProcess.ExitCode -eq 0)
  }
  $report.passed=($report.passed -and $report.closed_cleanly -and $report.errors.Count -eq 0)
  $report.lookup_retry_count=[DesktopNativeAcceptance]::LookupRetries
  if($WindowLayout) { $report.layout_passed=($layoutTraversalCompleted -and $findings.Count -eq 0) }
  $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $reportFile -Encoding UTF8
}
Write-Output "Native acceptance: $($checks.Count) checks; passed=$($report.passed); exit=$($report.exit_code)"
if(-not $report.passed) { Write-Output ($report.errors -join "`n"); exit 1 }
