// The 2D kingdom (Blu 11-04: "we already have a 3D kingdom, all we need is a 2D kingdom and it can be the same", seen
// from the side): the 3D kingdom itself through a flat side camera (_tools\kingdom2d\s_*.jpg and side.json, rendered by
// the Godot project with --side: the whole realm and a sharper picture of each district). Until those exist it shows
// the realm from above (index.json, teamshot\export_2d.py). The court are cut from their character sheets: the side
// pose while they walk, facing the way they go, the front pose when they stand. Everyone stands where the living realm
// puts them (repair-runs\realm-sim.json), walks the same roads at the same time as in 3D, and says the team's real
// messages to each other (repair-runs\threads.json). Nothing on screen is invented.
// The camera is the same director as the 3D kingdom: it goes to a conversation, else to whoever works or travels, and
// every third shot to the next member in turn. Click a member (or its name in the list) to follow it, drag to look
// around, the wheel zooms, double-click hands back to the director.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Web.Script.Serialization;

class Kingdom2D
{
    public class Member
    {
        public string Key, Name, Kind; public Color Color; public Bitmap Front, Side; public int Slot;
        public PointF Realm, Pos; public bool Moving, Left; public string Place = "", Doing = "", Why = "", Say = ""; public double SayUntil;
    }

    class Picture { public string Name, File; public RectangleF Rect; public Bitmap Img; public DateTime Used; }

    public readonly List<Member> Court = new List<Member>();
    public readonly List<string> Chat = new List<string>();
    public bool Ready { get { return pictures.Count > 0; } }
    public bool SideView { get { return side; } }
    public string Problem = "";

    const float S = 1.5f;
    readonly string dir;
    readonly List<Picture> pictures = new List<Picture>();
    readonly Dictionary<string, PointF> places = new Dictionary<string, PointF>();
    Bitmap mini;
    float size = 1024, sinP, cosP;
    bool side;
    float[] heights; int hn; float hstep = 2;
    RectangleF world;
    double clock, nextTalk = 2;

    PointF centre;
    float zoom = -1, userZoom = 7f;
    string mode = "director";
    List<string> shot = new List<string>();
    double shotAt = -100;
    int turn;
    RectangleF view;
    readonly List<Tuple<RectangleF, string>> rosterHits = new List<Tuple<RectangleF, string>>();
    RectangleF miniRect;

    class Message { public string Id, From, To, Text, Evidence, About; }
    readonly Queue<Message> waiting = new Queue<Message>();
    readonly HashSet<string> heard = new HashSet<string>();
    bool heardBefore;

    public Kingdom2D(string toolsDir, Func<string, Color> colorFor)
    {
        dir = Path.Combine(toolsDir, "kingdom2d");
        var js = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };
        try
        {
            var ix = js.Deserialize<Dictionary<string, object>>(File.ReadAllText(Path.Combine(dir, "index.json")));
            size = Convert.ToSingle(ix["size"]);
            foreach (var kv in (Dictionary<string, object>)ix["places"]) { var p = (ArrayList)kv.Value; places[kv.Key] = new PointF(Convert.ToSingle(p[0]), Convert.ToSingle(p[1])); }
            int slot = 0;
            foreach (var kv in (Dictionary<string, object>)ix["court"])
            {
                var c = (Dictionary<string, object>)kv.Value;
                var m = new Member { Key = kv.Key, Name = Str(c, "name"), Kind = Str(c, "kind"), Slot = slot++, Color = colorFor(kv.Key) };
                m.Front = LoadFast(Path.Combine(dir, "c_" + kv.Key + ".png"));
                m.Side = LoadFast(Path.Combine(dir, "c_" + kv.Key + "_side.png")) ?? m.Front;
                Court.Add(m);
            }
            using (var b = new Bitmap(Path.Combine(dir, Str(ix, "realm")))) mini = new Bitmap(b, 200, 200);
            string sideJson = Path.Combine(dir, "side.json"), hPath = Path.Combine(dir, "far_height.bin");
            if (File.Exists(sideJson) && File.Exists(hPath))
            {
                var sj = js.Deserialize<Dictionary<string, object>>(File.ReadAllText(sideJson));
                double pitch = Convert.ToDouble(sj["pitch"]); sinP = (float)Math.Sin(pitch); cosP = (float)Math.Cos(pitch);
                var raw = File.ReadAllBytes(hPath); heights = new float[raw.Length / 4]; Buffer.BlockCopy(raw, 0, heights, 0, raw.Length); hn = (int)Math.Sqrt(heights.Length);
                foreach (Dictionary<string, object> v in (ArrayList)sj["views"])
                {
                    float ppm = Convert.ToSingle(v["ppm"]), w = Convert.ToSingle(v["w"]) / ppm, h = Convert.ToSingle(v["h"]) / ppm;
                    float px = Convert.ToSingle(v["cx"]), pv = Convert.ToSingle(v["cz"]) * sinP - Convert.ToSingle(v["cy"]) * cosP;
                    pictures.Add(new Picture { Name = Str(v, "name"), File = Str(v, "file"), Rect = new RectangleF(px - w / 2, pv - h / 2, w, h) });
                }
                side = true;
            }
            else
            {
                pictures.Add(new Picture { Name = "realm", File = Str(ix, "realm"), Rect = new RectangleF(0, 0, size, size) });
                foreach (Dictionary<string, object> d in (ArrayList)ix["districts"])
                    pictures.Add(new Picture { Name = Str(d, "name"), File = Str(d, "img"), Rect = new RectangleF(Convert.ToSingle(d["x0"]), Convert.ToSingle(d["y0"]), Convert.ToSingle(d["n"]), Convert.ToSingle(d["n"])) });
                Problem = "Seen from above until the side pictures are made (they need a minute of the graphics card with no game running).";
            }
            world = pictures[0].Rect;
            centre = new PointF(world.X + world.Width / 2, world.Y + world.Height / 2);
            pictures[0].Img = LoadFast(Path.Combine(dir, pictures[0].File));
        }
        catch (Exception e) { Problem = "The 2D kingdom pictures are missing (" + dir + "): " + e.Message; pictures.Clear(); }
    }

    static Bitmap LoadFast(string path)
    {
        if (!File.Exists(path)) return null;
        using (var b = new Bitmap(path))
        {
            var o = new Bitmap(b.Width, b.Height, PixelFormat.Format32bppPArgb);
            using (var g = Graphics.FromImage(o)) g.DrawImage(b, 0, 0, b.Width, b.Height);
            return o;
        }
    }

    // A realm point (units) on the picture: from above it is the point itself; from the side it is where the side
    // camera sees it (metres across, and depth and height folded into one).
    PointF Project(PointF u)
    {
        if (!side) return u;
        return new PointF(u.X * S, u.Y * S * sinP - Height(u.X, u.Y) * cosP);
    }

    float Height(float x, float y)
    {
        if (heights == null) return 0;
        float gx = Math.Max(0, Math.Min(hn - 1.001f, x / hstep)), gy = Math.Max(0, Math.Min(hn - 1.001f, y / hstep));
        int ix = (int)gx, iy = (int)gy; float fx = gx - ix, fy = gy - iy;
        float a = heights[iy * hn + ix], b = heights[iy * hn + ix + 1], c = heights[(iy + 1) * hn + ix], d = heights[(iy + 1) * hn + ix + 1];
        return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy;
    }

    float PersonSize { get { return side ? 1.8f : 1.2f; } }

    // ---------------------------------------------------------------- the living realm
    public void Update(double dt, RectangleF bounds, Dictionary<string, object> sim, Dictionary<string, object> live, Func<string> fact)
    {
        clock += dt;
        double now = (DateTime.UtcNow - new DateTime(1970, 1, 1)).TotalSeconds;
        foreach (var m in Court)
        {
            var st = Get(sim, m.Key);
            var at = st != null && st.ContainsKey("at") ? st["at"] as ArrayList : null;
            if (at == null) continue;
            var pos = new PointF(Convert.ToSingle(at[0]), Convert.ToSingle(at[1]));
            var from = st.ContainsKey("from") ? st["from"] as ArrayList : null;
            double eta = Num(st, "eta"), depart = Num(st, "depart");
            m.Moving = from != null && now < eta;
            if (m.Moving)
            {
                float k = (float)Math.Max(0, Math.Min(1, (now - depart) / Math.Max(1, eta - depart)));
                float fx = Convert.ToSingle(from[0]), fy = Convert.ToSingle(from[1]);
                m.Left = pos.X < fx;
                pos = new PointF(fx + (pos.X - fx) * k, fy + (pos.Y - fy) * k);
            }
            else
            {
                double a = m.Slot * 0.9, r = (3.2 + (m.Slot % 3) * 0.8) / S;
                pos = new PointF(pos.X + (float)(Math.Cos(a) * r), pos.Y + (float)(Math.Sin(a) * r));
            }
            m.Realm = pos; m.Pos = Project(pos);
            m.Place = Str(st, "place"); m.Doing = m.Moving ? "on the road to the " + m.Place : Str(st, "doing"); m.Why = Str(st, "why");
        }
        Direct(dt, bounds);
        Speak(live, fact);
    }

    void Direct(double dt, RectangleF bounds)
    {
        if (!Ready || bounds.Width < 50) return;
        float fit = Math.Min(bounds.Width / world.Width, bounds.Height / world.Height);
        if (zoom < 0) zoom = fit;
        if (mode == "director" && (shot.Count == 0 || clock - shotAt > 14)) { Pick(); shotAt = clock; }
        if (mode == "free" || shot.Count == 0) return;
        var who = Court.Where(m => shot.Contains(m.Key)).ToList();
        if (who.Count == 0) return;
        var c = new PointF(who.Average(m => m.Pos.X), who.Average(m => m.Pos.Y) - PersonSize);
        float spread = who.Max(m => Dist(m.Pos, c)), near = side ? 30 : 22;
        float want = mode == "follow" ? userZoom : Math.Max(fit, Math.Min(side ? 9f : 12f, Math.Min(bounds.Width, bounds.Height) / (2 * (spread + near))));
        float gap = Dist(centre, c);
        if (gap > 120) want = Math.Min(want, Math.Max(fit, want / (1 + gap / 130)));
        float k = (float)(1 - Math.Exp(-dt * 1.6));
        centre = new PointF(centre.X + (c.X - centre.X) * k, centre.Y + (c.Y - centre.Y) * k);
        zoom = (float)Math.Exp(Math.Log(zoom) + (Math.Log(want) - Math.Log(zoom)) * (1 - Math.Exp(-dt * 1.2)));
    }

    // The same choice as the 3D director (main.gd pick_subject).
    void Pick()
    {
        var groups = new Dictionary<string, List<string>>();
        var busy = new List<List<string>>(); var everyone = new List<List<string>>();
        foreach (var m in Court)
        {
            var t = Regex.Match(m.Doing, @"\((T\d+)\)");
            if (m.Doing.StartsWith("talking") && t.Success) { if (!groups.ContainsKey(t.Groups[1].Value)) groups[t.Groups[1].Value] = new List<string>(); groups[t.Groups[1].Value].Add(m.Key); }
            else if (m.Doing == "working" || m.Doing == "judging" || m.Moving) busy.Add(new List<string> { m.Key });
            everyone.Add(new List<string> { m.Key });
        }
        turn++;
        var pool = groups.Count > 0 ? groups.Values.ToList() : busy;
        if (pool.Count == 0 || turn % 3 == 0) pool = everyone;
        if (pool.Count > 0) shot = pool[turn % pool.Count];
    }

    // A teammate's message: both are already where the living realm put them (free members meet in the district the
    // conversation is about); the sender says it, the teammate listens, and the director goes to them.
    void Speak(Dictionary<string, object> live, Func<string> fact)
    {
        if (clock < nextTalk) return;
        nextTalk = clock + 7;
        if (waiting.Count > 0)
        {
            var msg = waiting.Dequeue();
            var from = ByKind(msg.From); var to = ByKind(msg.To);
            Chat.Add(Short(DateTime.Now.ToString("HH:mm") + "  " + (from != null ? from.Name : "Claude Code") + " → " + (to != null ? to.Name : msg.To)
                     + " [" + msg.Id + (msg.About != "" ? ", " + msg.About : "") + "]: " + msg.Text + (msg.Evidence != "" ? "   (" + msg.Evidence + ")" : ""), 600));
            if (from != null) { from.Say = Short(msg.Text, 150); from.SayUntil = clock + 9; }
            if (to != null && clock > to.SayUntil) { to.Say = "(listening to " + (from != null ? from.Name : "Claude Code") + ")"; to.SayUntil = clock + 8; }
            if (mode == "director") { shot = Court.Where(m => m == from || m == to).Select(m => m.Key).ToList(); shotAt = clock; }
        }
        else
        {
            var members = Get(live, "members");
            var workers = Court.Where(m => clock > m.SayUntil && new[] { "working", "planning", "reviewing" }.Contains(Str(Get(members, m.Kind), "status"))).ToList();
            var helper = ByKind("ask");
            string news = fact();
            if (workers.Count > 0 && clock % 2 < 1)
            {
                var w = workers[(int)clock % workers.Count]; string task = Str(Get(members, w.Kind), "task");
                if (task != "") Say(w, (Str(Get(members, w.Kind), "status") == "reviewing" ? "Reviewing: " : "Working on: ") + Short(task, 110));
            }
            else if (helper != null && clock > helper.SayUntil && !string.IsNullOrEmpty(news)) Say(helper, "News: " + news);
        }
        while (Chat.Count > 80) Chat.RemoveAt(0);
    }

    // The Queen and the King share the boss role; the boss's messages are the Queen's (Fable 5.1).
    Member ByKind(string kind) { return Court.FirstOrDefault(m => m.Key == kind) ?? Court.FirstOrDefault(m => m.Kind == kind); }

    void Say(Member m, string text) { m.Say = Short(text, 150); m.SayUntil = clock + 7; Chat.Add(DateTime.Now.ToString("HH:mm") + "  " + m.Name + ": " + m.Say); }

    public void Hear(List<Dictionary<string, object>> threads)
    {
        var fresh = new List<Tuple<string, Message>>();
        foreach (var t in threads)
        {
            var msgs = t.ContainsKey("messages") ? t["messages"] as ArrayList : null;
            if (msgs == null) continue;
            foreach (var o in msgs)
            {
                var d = o as Dictionary<string, object>;
                if (d == null) continue;
                if (!heard.Add(Str(t, "id") + "|" + Str(d, "at") + "|" + Str(d, "from"))) continue;
                fresh.Add(Tuple.Create(Str(d, "at"), new Message { Id = Str(t, "id"), From = Str(d, "from"), To = Str(d, "to"), Text = Str(d, "text"), Evidence = Str(d, "evidence"), About = Str(t, "about") }));
            }
        }
        var ordered = fresh.OrderBy(x => x.Item1).Select(x => x.Item2).ToList();
        foreach (var m in heardBefore ? ordered : ordered.Skip(Math.Max(0, ordered.Count - 6))) waiting.Enqueue(m);
        heardBefore = true;
    }

    // ---------------------------------------------------------------- mouse
    public void Wheel(PointF at, int delta)
    {
        if (!Ready) return;
        var before = ToWorld(at);
        zoom = Math.Max(0.2f, Math.Min(30f, zoom * (delta > 0 ? 1.25f : 0.8f)));
        userZoom = zoom;
        if (mode == "director") mode = "free";
        if (mode == "free") { var after = ToWorld(at); centre = new PointF(centre.X + before.X - after.X, centre.Y + before.Y - after.Y); }
    }

    public void Drag(float dx, float dy) { if (!Ready) return; mode = "free"; centre = new PointF(centre.X - dx / zoom, centre.Y - dy / zoom); }

    public bool Click(PointF at)
    {
        foreach (var h in rosterHits) if (h.Item1.Contains(at)) { Follow(h.Item2); return true; }
        if (miniRect.Contains(at)) { mode = "free"; centre = Project(new PointF((at.X - miniRect.X) / miniRect.Width * size, (at.Y - miniRect.Y) / miniRect.Height * size)); return true; }
        float th = TokenHeight();
        var hit = Court.OrderBy(m => Dist(ToScreen(m.Pos), new PointF(at.X, at.Y + th / 2))).FirstOrDefault();
        if (hit != null && Dist(ToScreen(hit.Pos), new PointF(at.X, at.Y + th / 2)) < Math.Max(24, th * 0.7f)) { Follow(hit.Key); return true; }
        return false;
    }

    public void Director() { mode = "director"; shotAt = -100; }

    void Follow(string key) { mode = "follow"; shot = new List<string> { key }; userZoom = Math.Max(userZoom, side ? 5f : 6f); }

    PointF ToScreen(PointF w) { return new PointF(view.X + view.Width / 2 + (w.X - centre.X) * zoom, view.Y + view.Height / 2 + (w.Y - centre.Y) * zoom); }
    PointF ToWorld(PointF s) { return new PointF(centre.X + (s.X - view.X - view.Width / 2) / zoom, centre.Y + (s.Y - view.Y - view.Height / 2) / zoom); }
    float TokenHeight() { return Math.Max(34, Math.Min(140, zoom * PersonSize * 5f)); }

    // ---------------------------------------------------------------- drawing
    public void Draw(Graphics g, RectangleF bounds, Font font)
    {
        view = bounds;
        using (var bg = new SolidBrush(Color.FromArgb(10, 12, 18))) g.FillRectangle(bg, bounds);
        if (!Ready) { using (var br = new SolidBrush(Color.FromArgb(220, 226, 236))) g.DrawString(Problem, font, br, new RectangleF(bounds.X + 16, bounds.Y + 16, bounds.Width - 32, bounds.Height - 32)); return; }
        if (zoom < 0) zoom = Math.Min(bounds.Width / world.Width, bounds.Height / world.Height);
        var tl = ToWorld(new PointF(bounds.Left, bounds.Top)); var br2 = ToWorld(new PointF(bounds.Right, bounds.Bottom));
        var seen = RectangleF.FromLTRB(tl.X, tl.Y, br2.X, br2.Y);
        g.PixelOffsetMode = PixelOffsetMode.Half;
        var baseImg = pictures[0];
        float basePpm = baseImg.Img.Width / baseImg.Rect.Width;
        g.InterpolationMode = zoom > basePpm * 1.5f ? InterpolationMode.NearestNeighbor : InterpolationMode.Bilinear;
        Blit(g, baseImg, seen);
        if (zoom > basePpm * 0.9f)
            foreach (var p in pictures.Skip(1))
            {
                if (!p.Rect.IntersectsWith(seen)) continue;
                if (Load(p) != null) { g.InterpolationMode = zoom > p.Img.Width / p.Rect.Width * 1.5f ? InterpolationMode.NearestNeighbor : InterpolationMode.Bilinear; Blit(g, p, seen); }
            }
        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.TextRenderingHint = System.Drawing.Text.TextRenderingHint.ClearTypeGridFit;
        float th = TokenHeight();
        using (var pf = new Font(font.FontFamily, Math.Max(8f, Math.Min(13f, 7f + zoom)), FontStyle.Bold))
            foreach (var p in places)
            {
                var s = ToScreen(Project(p.Value)); var t = p.Key.ToUpper(); var sz = g.MeasureString(t, pf);
                if (!bounds.Contains(s)) continue;
                Outlined(g, t, pf, Color.FromArgb(210, 240, 226, 180), s.X - sz.Width / 2, s.Y - th * 1.5f - sz.Height);
            }
        foreach (var m in Court.OrderBy(x => x.Realm.Y)) DrawMember(g, m, ToScreen(m.Pos), th, font);
        foreach (var m in Court.Where(x => x.Say != "" && clock < x.SayUntil)) Bubble(g, m, ToScreen(m.Pos), th, font);
        DrawRoster(g, bounds, font);
        DrawMini(g, bounds);
        if (Problem != "") using (var f = new Font(font.FontFamily, 8f)) Outlined(g, Problem, f, Color.FromArgb(240, 200, 120), bounds.X + 10, bounds.Bottom - 20);
    }

    void Blit(Graphics g, Picture p, RectangleF seen)
    {
        var r = RectangleF.Intersect(seen, p.Rect);
        if (r.Width <= 0 || r.Height <= 0 || p.Img == null) return;
        float kx = p.Img.Width / p.Rect.Width, ky = p.Img.Height / p.Rect.Height;
        var src = new RectangleF((r.X - p.Rect.X) * kx, (r.Y - p.Rect.Y) * ky, r.Width * kx, r.Height * ky);
        var a = ToScreen(r.Location); var b = ToScreen(new PointF(r.Right, r.Bottom));
        g.DrawImage(p.Img, RectangleF.FromLTRB(a.X, a.Y, b.X, b.Y), src, GraphicsUnit.Pixel);
    }

    Bitmap Load(Picture p)
    {
        p.Used = DateTime.Now;
        if (p.Img != null) return p.Img;
        try { p.Img = LoadFast(Path.Combine(dir, p.File)); } catch { return null; }
        foreach (var old in pictures.Skip(1).Where(x => x.Img != null && x != p).OrderByDescending(x => x.Used).Skip(3).ToList()) { old.Img.Dispose(); old.Img = null; }
        return p.Img;
    }

    void DrawMember(Graphics g, Member m, PointF feet, float th, Font font)
    {
        if (!view.Contains(feet) && !view.Contains(new PointF(feet.X, feet.Y - th))) return;
        bool watched = shot.Contains(m.Key);
        float bob = m.Moving ? (float)Math.Abs(Math.Sin(clock * 7 + m.Slot)) * th * 0.04f : 0;
        using (var sh = new SolidBrush(Color.FromArgb(110, 0, 0, 0))) g.FillEllipse(sh, feet.X - th * 0.26f, feet.Y - th * 0.05f, th * 0.52f, th * 0.1f);
        using (var ring = new Pen(Color.FromArgb(watched ? 255 : 170, m.Color), watched ? 3f : 2f)) g.DrawEllipse(ring, feet.X - th * 0.28f, feet.Y - th * 0.06f, th * 0.56f, th * 0.12f);
        var img = m.Moving ? m.Side : m.Front;
        if (img != null)
        {
            float w = th * img.Width / img.Height;
            var dest = new RectangleF(feet.X - w / 2, feet.Y - th - bob, w, th);
            if (m.Moving && m.Left) g.DrawImage(img, new[] { new PointF(dest.Right, dest.Top), new PointF(dest.Left, dest.Top), new PointF(dest.Right, dest.Bottom) });
            else g.DrawImage(img, dest);
        }
        else using (var b = new SolidBrush(m.Color)) g.FillEllipse(b, feet.X - th * 0.2f, feet.Y - th * 0.6f, th * 0.4f, th * 0.4f);
        using (var nf = new Font(font.FontFamily, Math.Max(7.5f, Math.Min(10.5f, th / 8f)), FontStyle.Bold))
        {
            var sz = g.MeasureString(m.Name, nf);
            Outlined(g, m.Name, nf, m.Color, feet.X - sz.Width / 2, feet.Y + 2);
            if (th >= 50 || watched)
                using (var df = new Font(font.FontFamily, Math.Max(7f, Math.Min(9f, th / 9f))))
                {
                    string doing = Short(m.Doing, 46);
                    var dz = g.MeasureString(doing, df);
                    Outlined(g, doing, df, Color.FromArgb(225, 230, 238), feet.X - dz.Width / 2, feet.Y + 2 + sz.Height - 2);
                }
        }
    }

    static void Outlined(Graphics g, string text, Font f, Color c, float x, float y)
    {
        using (var path = new GraphicsPath())
        {
            path.AddString(text, f.FontFamily, (int)f.Style, g.DpiY * f.SizeInPoints / 72f, new PointF(x, y), StringFormat.GenericTypographic);
            using (var p = new Pen(Color.FromArgb(220, 0, 0, 0), 3.2f) { LineJoin = LineJoin.Round }) g.DrawPath(p, path);
            using (var b = new SolidBrush(c)) g.FillPath(b, path);
        }
    }

    void Bubble(Graphics g, Member m, PointF feet, float th, Font font)
    {
        using (var tf = new Font(font.FontFamily, 9f))
        {
            var sz = g.MeasureString(m.Say, tf, 260);
            float w = sz.Width + 14, h = sz.Height + 10;
            var r = new RectangleF(feet.X - w / 2, feet.Y - th - h - 14, w, h);
            r.X = Math.Max(view.X + 4, Math.Min(view.Right - w - 4, r.X)); r.Y = Math.Max(view.Y + 4, r.Y);
            using (var path = new GraphicsPath())
            {
                float rad = 10;
                path.AddArc(r.X, r.Y, rad, rad, 180, 90); path.AddArc(r.Right - rad, r.Y, rad, rad, 270, 90);
                path.AddArc(r.Right - rad, r.Bottom - rad, rad, rad, 0, 90); path.AddArc(r.X, r.Bottom - rad, rad, rad, 90, 90);
                path.CloseFigure();
                using (var b = new SolidBrush(Color.FromArgb(235, 22, 26, 36))) g.FillPath(b, path);
                using (var p = new Pen(m.Color, 1.6f)) g.DrawPath(p, path);
            }
            using (var b = new SolidBrush(Color.FromArgb(235, 22, 26, 36))) g.FillPolygon(b, new[] { new PointF(feet.X - 6, r.Bottom - 1), new PointF(feet.X + 6, r.Bottom - 1), new PointF(feet.X, r.Bottom + 9) });
            using (var b = new SolidBrush(Color.FromArgb(235, 236, 240))) g.DrawString(m.Say, tf, b, new RectangleF(r.X + 7, r.Y + 5, sz.Width + 2, sz.Height + 2));
        }
    }

    // Everyone, wherever they are: the list shows each member's district and what it is doing; click a name to follow.
    void DrawRoster(Graphics g, RectangleF bounds, Font font)
    {
        rosterHits.Clear();
        bool small = bounds.Width < 900 || bounds.Height < 420;
        using (var f = new Font(font.FontFamily, small ? 7.5f : 8.5f))
        using (var hf = new Font(font.FontFamily, small ? 7.5f : 8.5f, FontStyle.Bold))
        {
            float lh = f.GetHeight(g) + (small ? 0 : 1), w = small ? Math.Min(210, bounds.Width * 0.3f) : Math.Min(330, bounds.Width * 0.42f), h = lh * (Court.Count + 2) + 10;
            var box = new RectangleF(bounds.X + 8, bounds.Y + 8, w, h);
            using (var b = new SolidBrush(Color.FromArgb(small ? 150 : 185, 12, 14, 20))) g.FillRectangle(b, box);
            string head = mode == "director" ? "Director: following the action" : mode == "follow" ? "Following (double-click: director)" : "Looking around (double-click: director)";
            g.DrawString(head, hf, Brushes.White, box.X + 6, box.Y + 4);
            using (var dim = new SolidBrush(Color.FromArgb(150, 160, 175))) g.DrawString("click a name to follow · drag · wheel zoom", f, dim, box.X + 6, box.Y + 4 + lh);
            float y = box.Y + 6 + lh * 2;
            foreach (var m in Court)
            {
                var row = new RectangleF(box.X + 2, y, w - 4, lh);
                if (shot.Contains(m.Key)) using (var sel = new SolidBrush(Color.FromArgb(60, m.Color))) g.FillRectangle(sel, row);
                using (var c = new SolidBrush(m.Color)) g.DrawString(m.Name, hf, c, row.X + 4, y);
                float nx = row.X + 4 + g.MeasureString(m.Name, hf).Width;
                using (var t = new SolidBrush(Color.FromArgb(205, 212, 222))) g.DrawString(Short(" " + (small ? m.Doing : m.Place + " · " + m.Doing), Math.Max(8, (int)((w - nx + box.X) / (small ? 5f : 5.6f)))), f, t, nx, y);
                rosterHits.Add(Tuple.Create(row, m.Key));
                y += lh;
            }
        }
    }

    // The realm from above in the corner: every member's dot and the part the big view shows.
    void DrawMini(Graphics g, RectangleF bounds)
    {
        float ms = Math.Min(170, Math.Min(bounds.Width, bounds.Height) * 0.28f);
        miniRect = new RectangleF(bounds.Right - ms - 8, bounds.Bottom - ms - 8, ms, ms);
        g.DrawImage(mini, miniRect);
        using (var p = new Pen(Color.FromArgb(160, 255, 255, 255))) g.DrawRectangle(p, miniRect.X, miniRect.Y, miniRect.Width, miniRect.Height);
        float k = ms / size;
        foreach (var m in Court)
            using (var b = new SolidBrush(m.Color)) g.FillEllipse(b, miniRect.X + m.Realm.X * k - 3, miniRect.Y + m.Realm.Y * k - 3, 6, 6);
        var tl = ToWorld(new PointF(bounds.Left, bounds.Top)); var br = ToWorld(new PointF(bounds.Right, bounds.Bottom));
        float x0 = side ? tl.X / S : tl.X, x1 = side ? br.X / S : br.X, y0 = side ? tl.Y / (S * sinP) : tl.Y, y1 = side ? br.Y / (S * sinP) : br.Y;
        var vr = RectangleF.FromLTRB(miniRect.X + Math.Max(0, x0) * k, miniRect.Y + Math.Max(0, y0) * k, miniRect.X + Math.Min(size, x1) * k, miniRect.Y + Math.Min(size, y1) * k);
        if (vr.Width > 0 && vr.Height > 0) using (var p = new Pen(Color.FromArgb(230, 255, 214, 90), 1.5f)) g.DrawRectangle(p, vr.X, vr.Y, vr.Width, vr.Height);
    }

    static string Short(string s, int n) { s = (s ?? "").Replace("\r", " ").Replace("\n", " ").Trim(); return s.Length <= n ? s : s.Substring(0, n - 1) + "…"; }
    static float Dist(PointF a, PointF b) { float dx = a.X - b.X, dy = a.Y - b.Y; return (float)Math.Sqrt(dx * dx + dy * dy); }
    static double Num(Dictionary<string, object> d, string key) { object v; double x; return d != null && d.TryGetValue(key, out v) && v != null && double.TryParse(Convert.ToString(v, System.Globalization.CultureInfo.InvariantCulture), System.Globalization.NumberStyles.Float, System.Globalization.CultureInfo.InvariantCulture, out x) ? x : 0; }
    static Dictionary<string, object> Get(Dictionary<string, object> d, string key) { object v; return d != null && d.TryGetValue(key, out v) ? v as Dictionary<string, object> : null; }
    static string Str(Dictionary<string, object> d, string key) { object v; return d != null && d.TryGetValue(key, out v) && v != null ? v.ToString() : ""; }
}
