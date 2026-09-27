using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using HarmonyLib;
using MelonLoader;
using Protocol2;
using UnityEngine;

[assembly: MelonInfo(typeof(RccAgentBridge.BridgeMod), "RccAgentBridge", "1.0.0", "RCC")]
[assembly: MelonGame(null, null)]

namespace RccAgentBridge
{
    public class BridgeMod : MelonMod
    {
        public static BridgeMod Instance;
        public int HookCount;
        readonly List<string> hooks = new List<string>();
        readonly List<string> logs = new List<string>();
        readonly Queue<Job> jobs = new Queue<Job>();
        readonly object gate = new object();
        TcpListener listener;
        volatile bool running;

        public override void OnInitializeMelon()
        {
            Instance = this;
            InstallHooks();
            try
            {
                Application.logMessageReceived += OnUnityLog;
            }
            catch (Exception ex)
            {
                LoggerInstance.Msg("Unity log hook skipped: " + ex.Message);
            }
            running = true;
            listener = new TcpListener(IPAddress.Loopback, 27140);
            listener.Start();
            var thread = new Thread(AcceptLoop);
            thread.IsBackground = true;
            thread.Name = "RccAgentBridge";
            thread.Start();
            LoggerInstance.Msg("RccAgentBridge listening on 127.0.0.1:27140");
        }

        public override void OnApplicationQuit()
        {
            running = false;
            try
            {
                if (listener != null)
                    listener.Stop();
            }
            catch (Exception)
            {
            }
        }

        public override void OnUpdate()
        {
            while (true)
            {
                Job job = null;
                lock (gate)
                {
                    if (jobs.Count > 0)
                        job = jobs.Dequeue();
                }
                if (job == null)
                    return;
                try
                {
                    job.Response = Dispatch(job.Fields);
                }
                catch (Exception ex)
                {
                    job.Response = Error(job.Fields, ex.GetType().Name + ": " + ex.Message);
                }
                job.Done.Set();
            }
        }

        void AcceptLoop()
        {
            while (running)
            {
                TcpClient client;
                try
                {
                    client = listener.AcceptTcpClient();
                }
                catch (Exception)
                {
                    if (!running)
                        return;
                    continue;
                }
                ThreadPool.QueueUserWorkItem(HandleClient, client);
            }
        }

        void HandleClient(object state)
        {
            var client = (TcpClient)state;
            try
            {
                client.ReceiveTimeout = 5000;
                client.SendTimeout = 5000;
                using (client)
                using (var stream = client.GetStream())
                {
                    var encoding = new UTF8Encoding(false);
                    var reader = new StreamReader(stream, encoding);
                    string line = reader.ReadLine();
                    string response;
                    if (string.IsNullOrEmpty(line))
                        response = "{\"ok\":false,\"error\":\"empty request\"}";
                    else
                        response = HandleLine(line);
                    var writer = new StreamWriter(stream, encoding);
                    writer.WriteLine(response);
                    writer.Flush();
                }
            }
            catch (Exception ex)
            {
                LoggerInstance.Warning("bridge client: " + ex.Message);
            }
        }

        string HandleLine(string line)
        {
            Dictionary<string, string> fields;
            try
            {
                fields = Json.Parse(line);
            }
            catch (Exception ex)
            {
                return "{\"ok\":false,\"error\":" + Json.Quote(ex.Message) + "}";
            }
            string op = Get(fields, "op");
            if (op == "serialize" || op == "eval")
            {
                var job = new Job();
                job.Fields = fields;
                job.Done = new ManualResetEvent(false);
                lock (gate)
                    jobs.Enqueue(job);
                if (!job.Done.WaitOne(4000))
                    return Error(fields, "unity thread did not run the request");
                return job.Response;
            }
            try
            {
                return Dispatch(fields);
            }
            catch (Exception ex)
            {
                return Error(fields, ex.GetType().Name + ": " + ex.Message);
            }
        }

        string Dispatch(Dictionary<string, string> fields)
        {
            string op = Get(fields, "op");
            string id = Get(fields, "id");
            if (op == "status")
            {
                string scene = "";
                try
                {
                    scene = Application.loadedLevelName;
                }
                catch (Exception)
                {
                }
                return Ok(id, "\"scene\":" + Json.Quote(scene) + ",\"hooks\":" + HookCount + ",\"listening\":true");
            }
            if (op == "hooks")
                return Ok(id, "\"hooks\":" + Snapshot(hooks));
            if (op == "log")
                return Ok(id, "\"lines\":" + Snapshot(logs));
            if (op == "search")
                return Ok(id, "\"types\":" + Json.Array(SearchTypes(Get(fields, "query"))));
            if (op == "describe")
                return Describe(id, Get(fields, "name"));
            if (op == "serialize")
                return Serialize(id, Get(fields, "category"));
            if (op == "eval")
                return Eval(id, Get(fields, "code"));
            return Error(fields, "unknown op " + op);
        }

        string Describe(string id, string name)
        {
            Type type = FindType(name);
            if (type == null)
                return "{\"ok\":false,\"id\":" + Json.Quote(id) + ",\"error\":" + Json.Quote("type not found") + "}";
            var methods = new List<string>();
            foreach (MethodInfo method in type.GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly))
            {
                if (methods.Count >= 80)
                    break;
                methods.Add(method.ToString());
            }
            var typeFields = new List<string>();
            foreach (FieldInfo field in type.GetFields(BindingFlags.Public | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly))
            {
                if (typeFields.Count >= 80)
                    break;
                typeFields.Add(field.FieldType.Name + " " + field.Name);
            }
            return Ok(id, "\"type\":" + Json.Quote(type.FullName) + ",\"methods\":" + Json.Array(methods) + ",\"fields\":" + Json.Array(typeFields));
        }

        string Serialize(string id, string categoryText)
        {
            int category = int.Parse(categoryText);
            Card card = Card.Create((CardCategory)category);
            if (card == null)
                return "{\"ok\":false,\"id\":" + Json.Quote(id) + ",\"error\":\"Card.Create returned null\"}";
            if (card.Id == null)
                card.Id = "agent";
            CardHorse horse = card as CardHorse;
            if (horse != null)
            {
                if (horse.Stats == null)
                    horse.Stats = new Protocol2.HorseLogic();
                if (horse.Motor == null)
                    horse.Motor = new Protocol2.HorseMotor();
                if (horse.Anim == null)
                    horse.Anim = new Protocol2.HorseAnim();
            }
            var stream = new MemoryStream();
            var writer = new BinaryWriter(stream);
            card.Write(writer);
            writer.Flush();
            return Ok(id, "\"category\":" + category + ",\"type\":" + Json.Quote(card.GetType().FullName) + ",\"hex\":" + Json.Quote(ToHex(stream.ToArray())));
        }

        string Eval(string id, string code)
        {
            Assembly explorer = null;
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                if (assembly.GetName().Name == "UnityExplorer.ML.Mono")
                {
                    explorer = assembly;
                    break;
                }
            }
            if (explorer == null)
                return "{\"ok\":false,\"id\":" + Json.Quote(id) + ",\"error\":\"UnityExplorer is not loaded, so Mono.CSharp is unavailable\"}";
            Type settingsType = explorer.GetType("Mono.CSharp.CompilerSettings");
            Type printerType = explorer.GetType("Mono.CSharp.ConsoleReportPrinter");
            Type contextType = explorer.GetType("Mono.CSharp.CompilerContext");
            Type evaluatorType = explorer.GetType("Mono.CSharp.Evaluator");
            var writer = new StringWriter();
            object settings = Activator.CreateInstance(settingsType);
            object printer = Activator.CreateInstance(printerType, new object[] { writer });
            object context = Activator.CreateInstance(contextType, new object[] { settings, printer });
            object evaluator = Activator.CreateInstance(evaluatorType, new object[] { context });
            PropertyInfo interactiveBase = evaluatorType.GetProperty("InteractiveBaseClass");
            interactiveBase.SetValue(evaluator, typeof(object), null);
            MethodInfo reference = evaluatorType.GetMethod("ReferenceAssembly");
            reference.Invoke(evaluator, new object[] { typeof(Card).Assembly });
            reference.Invoke(evaluator, new object[] { typeof(GameObject).Assembly });
            MethodInfo evaluate = null;
            foreach (MethodInfo method in evaluatorType.GetMethods())
            {
                if (method.Name == "Evaluate" && method.GetParameters().Length == 3)
                {
                    evaluate = method;
                    break;
                }
            }
            var args = new object[] { code, null, false };
            object compiled = evaluate.Invoke(evaluator, args);
            string shown = args[1] == null ? "" : args[1].ToString();
            string detail = compiled == null ? writer.ToString() : compiled.ToString();
            return Ok(id, "\"result\":" + Json.Quote(shown) + ",\"set\":" + (((bool)args[2]) ? "true" : "false") + ",\"detail\":" + Json.Quote(detail));
        }

        void InstallHooks()
        {
            Assembly game = null;
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                if (assembly.GetName().Name == "Assembly-CSharp")
                {
                    game = assembly;
                    break;
                }
            }
            if (game == null)
            {
                LoggerInstance.Warning("Assembly-CSharp was not loaded; send hooks were not installed");
                return;
            }
            var withArgs = new HarmonyMethod(typeof(CallLog).GetMethod("Prefix"));
            int count = 0;
            foreach (Type type in game.GetTypes())
            {
                if (!IsService(type))
                    continue;
                foreach (MethodInfo method in type.GetMethods(BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly))
                {
                    if (method.IsSpecialName || method.IsAbstract)
                        continue;
                    try
                    {
                        HarmonyInstance.Patch(method, withArgs);
                        count++;
                    }
                    catch (Exception ex)
                    {
                        LoggerInstance.Warning("hook " + type.Name + "." + method.Name + " failed: " + ex.Message);
                    }
                }
            }
            HookCount = count;
            LoggerInstance.Msg("Installed " + count + " service hooks");
            Type loca = game.GetType("L");
            if (loca != null)
            {
                MethodInfo get = loca.GetMethod("Get", new Type[] { typeof(string) });
                if (get != null)
                    HarmonyInstance.Patch(get, null, new HarmonyMethod(typeof(LocaleFallback).GetMethod("Postfix")));
            }
        }

        static bool IsService(Type type)
        {
            for (Type current = type; current != null; current = current.BaseType)
            {
                if (current.FullName == "Igor.Service")
                    return true;
            }
            return false;
        }

        public void Record(MethodBase method, object[] args)
        {
            var parameters = method.GetParameters();
            var parts = new List<string>();
            if (args != null)
            {
                for (int i = 0; i < args.Length; i++)
                {
                    string param = i < parameters.Length ? parameters[i].Name : "arg";
                    parts.Add(param + "=" + Redact(param, args[i]));
                }
            }
            string line = method.DeclaringType.Name + "." + method.Name + "(" + string.Join(", ", parts.ToArray()) + ")";
            lock (gate)
            {
                hooks.Add(line);
                if (hooks.Count > 200)
                    hooks.RemoveAt(0);
            }
        }

        static string Redact(string name, object value)
        {
            if (value == null)
                return "null";
            string text = value.ToString();
            string lower = name == null ? "" : name.ToLower();
            if (lower.IndexOf("token") >= 0 || lower.IndexOf("ticket") >= 0 || lower.IndexOf("access") >= 0)
                return "[redacted]";
            if (text.Length > 80)
                return "[redacted]";
            return text;
        }

        void OnUnityLog(string condition, string stackTrace, LogType type)
        {
            if (type != LogType.Exception && type != LogType.Error)
                return;
            string line = type + ": " + condition;
            lock (gate)
            {
                logs.Add(line);
                if (logs.Count > 100)
                    logs.RemoveAt(0);
            }
        }

        string Snapshot(List<string> source)
        {
            string[] copy;
            lock (gate)
                copy = source.ToArray();
            return Json.Array(new List<string>(copy));
        }

        static List<string> SearchTypes(string query)
        {
            var found = new List<string>();
            if (query == null)
                query = "";
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                if (assembly.GetName().Name != "Assembly-CSharp")
                    continue;
                foreach (Type type in assembly.GetTypes())
                {
                    if (type.FullName != null && type.FullName.IndexOf(query) >= 0)
                    {
                        found.Add(type.FullName);
                        if (found.Count >= 50)
                            return found;
                    }
                }
            }
            return found;
        }

        static Type FindType(string name)
        {
            if (string.IsNullOrEmpty(name))
                return null;
            foreach (Assembly assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type type = assembly.GetType(name);
                if (type != null)
                    return type;
            }
            return null;
        }

        static string Get(Dictionary<string, string> fields, string key)
        {
            string value;
            if (fields != null && fields.TryGetValue(key, out value))
                return value;
            return "";
        }

        static string Ok(string id, string body)
        {
            return "{\"ok\":true,\"id\":" + Json.Quote(id) + "," + body + "}";
        }

        static string Error(Dictionary<string, string> fields, string message)
        {
            return "{\"ok\":false,\"id\":" + Json.Quote(Get(fields, "id")) + ",\"error\":" + Json.Quote(message) + "}";
        }

        static string ToHex(byte[] data)
        {
            var builder = new StringBuilder(data.Length * 2);
            for (int i = 0; i < data.Length; i++)
                builder.Append(data[i].ToString("x2"));
            return builder.ToString();
        }

        class Job
        {
            public Dictionary<string, string> Fields;
            public ManualResetEvent Done;
            public string Response;
        }
    }

    public static class LocaleFallback
    {
        public static void Postfix(string key, ref string __result)
        {
            if (__result != key || key == null)
                return;
            __result = key.Replace('_', ' ');
        }
    }

    public static class CallLog
    {
        public static void Prefix(MethodBase __originalMethod, object[] __args)
        {
            if (BridgeMod.Instance != null)
                BridgeMod.Instance.Record(__originalMethod, __args);
        }
    }

    static class Json
    {
        public static Dictionary<string, string> Parse(string text)
        {
            var fields = new Dictionary<string, string>();
            int i = 0;
            Skip(text, ref i);
            Expect(text, ref i, '{');
            Skip(text, ref i);
            if (i < text.Length && text[i] == '}')
                return fields;
            while (i < text.Length)
            {
                string key = ReadString(text, ref i);
                Skip(text, ref i);
                Expect(text, ref i, ':');
                Skip(text, ref i);
                fields[key] = ReadValue(text, ref i);
                Skip(text, ref i);
                if (i < text.Length && text[i] == ',')
                {
                    i++;
                    Skip(text, ref i);
                    continue;
                }
                break;
            }
            return fields;
        }

        public static string Quote(string value)
        {
            if (value == null)
                return "null";
            var builder = new StringBuilder(value.Length + 2);
            builder.Append('"');
            for (int i = 0; i < value.Length; i++)
            {
                char c = value[i];
                if (c == '\\' || c == '"')
                {
                    builder.Append('\\');
                    builder.Append(c);
                }
                else if (c == '\n')
                    builder.Append("\\n");
                else if (c == '\r')
                    builder.Append("\\r");
                else if (c == '\t')
                    builder.Append("\\t");
                else
                    builder.Append(c);
            }
            builder.Append('"');
            return builder.ToString();
        }

        public static string Array(List<string> values)
        {
            var builder = new StringBuilder();
            builder.Append('[');
            for (int i = 0; i < values.Count; i++)
            {
                if (i > 0)
                    builder.Append(',');
                builder.Append(Quote(values[i]));
            }
            builder.Append(']');
            return builder.ToString();
        }

        static string ReadValue(string text, ref int i)
        {
            if (text[i] == '"')
                return ReadString(text, ref i);
            int start = i;
            while (i < text.Length && text[i] != ',' && text[i] != '}' && text[i] != ' ' && text[i] != '\n' && text[i] != '\r')
                i++;
            return text.Substring(start, i - start);
        }

        static string ReadString(string text, ref int i)
        {
            Expect(text, ref i, '"');
            var builder = new StringBuilder();
            while (i < text.Length)
            {
                char c = text[i++];
                if (c == '"')
                    return builder.ToString();
                if (c == '\\' && i < text.Length)
                {
                    char escaped = text[i++];
                    if (escaped == 'n')
                        builder.Append('\n');
                    else if (escaped == 'r')
                        builder.Append('\r');
                    else if (escaped == 't')
                        builder.Append('\t');
                    else
                        builder.Append(escaped);
                }
                else
                    builder.Append(c);
            }
            throw new FormatException("unterminated string");
        }

        static void Expect(string text, ref int i, char expected)
        {
            if (i >= text.Length || text[i] != expected)
                throw new FormatException("expected " + expected);
            i++;
        }

        static void Skip(string text, ref int i)
        {
            while (i < text.Length && (text[i] == ' ' || text[i] == '\n' || text[i] == '\r' || text[i] == '\t'))
                i++;
        }
    }
}
