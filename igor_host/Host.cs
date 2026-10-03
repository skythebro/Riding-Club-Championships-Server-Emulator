using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Web.Script.Serialization;

// Headless codec for the Riding Club Championships client.
// Assembly-CSharp.dll loads on the desktop CLR. Card.Create(LogicHorse) returns
// Protocol2.CardHorse, and Igor.Write / ReplySpecialOffers.Write run without a
// UnityEngine stub. Igor.Transport.PopRecvData returns MemoryStream.GetBuffer(),
// which is longer than the frame, so framing here follows Transport.Receive and
// keeps MemoryStream.ToArray() (the exact body).
public static class Host
{
    const uint BodyMaxSize = 100000000U;
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    static Assembly Game;
    static string ManagedDir;
    static readonly Dictionary<int, CallInfo> Catalog = new Dictionary<int, CallInfo>();
    static readonly Dictionary<string, FrameBuffer> Clients = new Dictionary<string, FrameBuffer>();
    static readonly object Gate = new object();
    static Dictionary<string, object> BalanceData = new Dictionary<string, object>();

    static void LoadBalance()
    {
        string path = Path.GetFullPath(Path.Combine(ManagedDir ?? "", "..", "..", "ServerEmu", "balance.json"));
        if (!File.Exists(path))
            return;
        Json.MaxJsonLength = int.MaxValue;
        Dictionary<string, object> parsed = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path));
        if (parsed != null)
            BalanceData = parsed;
    }

    static object BalNode(params string[] path)
    {
        object node = BalanceData;
        foreach (string key in path)
        {
            Dictionary<string, object> dict = node as Dictionary<string, object>;
            if (dict == null || !dict.ContainsKey(key))
                return null;
            node = dict[key];
        }
        return node;
    }

    static string BalString(string fallback, params string[] path)
    {
        object node = BalNode(path);
        if (node == null)
            return fallback;
        string text = Convert.ToString(node);
        return string.IsNullOrEmpty(text) ? fallback : text;
    }

    static int ItemPrice(string id, int fallback)
    {
        object node = BalNode("items", id, "price");
        if (node == null)
            return fallback;
        return Convert.ToInt32(node);
    }

    static int BalInt(int fallback, params string[] path)
    {
        object node = BalNode(path);
        if (node == null)
            return fallback;
        return Convert.ToInt32(node);
    }

    static float BalFloat(float fallback, params string[] path)
    {
        object node = BalNode(path);
        if (node == null)
            return fallback;
        return Convert.ToSingle(node);
    }

    static int[] BalInts(int[] fallback, params string[] path)
    {
        object node = BalNode(path);
        if (!(node is IEnumerable) || node is string)
            return fallback;
        var values = new List<int>();
        foreach (object item in (IEnumerable)node)
            values.Add(Convert.ToInt32(item));
        return values.Count == 0 ? fallback : values.ToArray();
    }

    static void SetPriceCoins(object owner, string propertyName, int coins)
    {
        PropertyInfo property = owner.GetType().GetProperty(propertyName);
        if (property == null)
            return;
        object price = property.GetValue(owner, null);
        if (price == null)
        {
            price = Activator.CreateInstance(property.PropertyType);
            property.SetValue(owner, price, null);
        }
        PropertyInfo coinsProperty = price.GetType().GetProperty("Coins");
        if (coinsProperty != null)
            coinsProperty.SetValue(price, coins, null);
    }

    public static int Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.Error.WriteLine("usage: IgorHost.exe <managed-dir> <port>");
            return 2;
        }
        ManagedDir = args[0];
        string managed = ManagedDir;
        int port = int.Parse(args[1]);
        AppDomain.CurrentDomain.AssemblyResolve += (sender, resolveArgs) =>
        {
            string file = Path.Combine(managed, new AssemblyName(resolveArgs.Name).Name + ".dll");
            return File.Exists(file) ? Assembly.LoadFrom(file) : null;
        };
        Game = Assembly.LoadFrom(Path.Combine(managed, "Assembly-CSharp.dll"));
        LoadCatalog();

        var listener = new TcpListener(IPAddress.Loopback, port);
        listener.Start();
        Console.WriteLine("igor-host " + port);
        Console.Out.Flush();
        while (true)
        {
            TcpClient client = listener.AcceptTcpClient();
            var thread = new System.Threading.Thread(() => Serve(client));
            thread.IsBackground = true;
            thread.Start();
        }
    }

    static void Serve(TcpClient client)
    {
        client.NoDelay = true;
        try
        {
            using (client)
            using (var stream = client.GetStream())
            using (var reader = new StreamReader(stream, new UTF8Encoding(false)))
            using (var writer = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true })
            {
                string line;
                while ((line = reader.ReadLine()) != null)
                {
                    if (line.Length == 0)
                        continue;
                    writer.WriteLine(HandleLine(line));
                }
            }
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("client closed: " + ex.Message);
        }
    }

    static string HandleLine(string line)
    {
        Dictionary<string, object> request;
        try
        {
            request = (Dictionary<string, object>)Json.DeserializeObject(line);
        }
        catch (Exception ex)
        {
            return Json.Serialize(new Dictionary<string, object> { { "ok", false }, { "error", ex.Message } });
        }
        object id = request.ContainsKey("id") ? request["id"] : null;
        try
        {
            string op = Convert.ToString(request["op"]);
            Dictionary<string, object> result;
            if (op == "ping")
                result = new Dictionary<string, object> { { "ok", true }, { "pong", true } };
            else if (op == "feed")
                result = Feed(Convert.ToString(request["client"]), ParseHex(Convert.ToString(request["hex"])));
            else if (op == "drop")
            {
                lock (Gate)
                    Clients.Remove(Convert.ToString(request["client"]));
                result = new Dictionary<string, object> { { "ok", true } };
            }
            else if (op == "reply")
                result = Reply(request);
            else if (op == "push")
                result = Push(request);
            else if (op == "roundtrip")
                result = RoundTrip(request);
            else
                result = new Dictionary<string, object> { { "ok", false }, { "error", "unknown op " + op } };
            if (id != null)
                result["id"] = id;
            return Json.Serialize(result);
        }
        catch (Exception ex)
        {
            var fail = new Dictionary<string, object> { { "ok", false }, { "error", ex.GetType().Name + ": " + ex.Message } };
            if (id != null)
                fail["id"] = id;
            return Json.Serialize(fail);
        }
    }

    static Dictionary<string, object> Feed(string clientId, byte[] data)
    {
        FrameBuffer buffer;
        lock (Gate)
        {
            if (!Clients.TryGetValue(clientId, out buffer))
            {
                buffer = new FrameBuffer();
                Clients[clientId] = buffer;
            }
        }
        List<byte[]> packets = buffer.Take(data);
        var messages = new ArrayList();
        foreach (byte[] packet in packets)
            messages.Add(Decode(packet));
        return new Dictionary<string, object> { { "ok", true }, { "messages", messages } };
    }

    static Dictionary<string, object> Decode(byte[] packet)
    {
        if (packet.Length < 2)
        {
            return new Dictionary<string, object> {
                { "name", "Unknown" },
                { "service", packet.Length > 0 ? (int)packet[0] : 0 },
                { "function", -1 },
                { "hex", ToHex(packet) }
            };
        }
        using (var ms = new MemoryStream(packet))
        using (var reader = new BinaryReader(ms))
        {
            int service = reader.ReadByte();
            int function = reader.ReadByte();
            var message = new Dictionary<string, object> { { "service", service }, { "function", function } };
            if (service == 100 && function == 0)
            {
                message["name"] = "Login";
                message["rpc"] = (int)reader.ReadUInt16();
                message["version"] = Convert.ToInt32(Call("Igor.Read", "Int", reader));
                message["source"] = (int)reader.ReadByte();
                message["sourceId"] = Convert.ToUInt64(Call("Igor.Read", "ULong", reader)).ToString();
                message["token"] = Convert.ToString(Call("Igor.Read", "String", reader));
                return message;
            }
            if (service == 105 && function == 9)
            {
                message["name"] = "UpdateAccountInfo";
                message["playerName"] = Convert.ToString(Call("Igor.Read", "String", reader));
                string avatar = null;
                if (reader.ReadBoolean())
                    avatar = Convert.ToString(Call("Igor.Read", "String", reader));
                message["avatar"] = avatar;
                message["gender"] = (int)reader.ReadByte();
                message["locale"] = Convert.ToString(Call("Igor.Read", "String", reader));
                return message;
            }
            if (service == 109 && function == 11)
            {
                message["name"] = "SubscribeFriends";
                int count = Convert.ToInt32(Call("Igor.Read", "Size", reader));
                var ids = new ArrayList();
                for (int i = 0; i < count; i++)
                    ids.Add(Convert.ToUInt64(Call("Igor.Read", "ULong", reader)).ToString());
                message["friendIds"] = ids;
                return message;
            }
            if (service == 108 && function == 0)
            {
                message["name"] = "Subscribe";
                return message;
            }
            if (service == 108 && function == 9)
            {
                message["name"] = "GetSpecialOffers";
                message["rpc"] = (int)reader.ReadUInt16();
                return message;
            }
            if (service == 106 && function == 11)
            {
                message["name"] = "GetLikes";
                message["rpc"] = (int)reader.ReadUInt16();
                return message;
            }
            CallInfo info;
            if (Catalog.TryGetValue((service << 8) | function, out info))
            {
                message["name"] = info.Name;
                message["emptyReply"] = info.EmptyReply;
                message["placeholder"] = info.Placeholder ?? "";
                message["payloadKind"] = info.PayloadKind ?? "none";
                message["payloadType"] = info.PayloadType ?? "";
                if (info.HasRpc && reader.BaseStream.Position + 2 <= reader.BaseStream.Length)
                    message["rpc"] = (int)reader.ReadUInt16();
                if (info.Name == "ApplyAvatar")
                    ReadAvatar(reader, message);
                byte[] rest = reader.ReadBytes((int)(reader.BaseStream.Length - reader.BaseStream.Position));
                message["rest"] = ToHex(rest);
                return message;
            }
            message["name"] = "Unknown";
            var unknown = new byte[Math.Max(0, packet.Length - 2)];
            if (unknown.Length > 0)
                Buffer.BlockCopy(packet, 2, unknown, 0, unknown.Length);
            message["rest"] = ToHex(unknown);
            return message;
        }
    }

    static Dictionary<string, object> Reply(Dictionary<string, object> request)
    {
        string kind = Convert.ToString(request["kind"]);
        byte[] payload;
        byte service;
        byte function;
        byte status = 0;
        ushort rpc = request.ContainsKey("rpc") ? Convert.ToUInt16(request["rpc"]) : (ushort)0;
        if (kind == "login")
        {
            service = 100;
            function = 0;
            payload = WriteLogin(Convert.ToUInt32(request["playerId"]), Convert.ToInt32(request["userState"]), Convert.ToInt32(request["access"]));
        }
        else if (kind == "special_offers")
        {
            service = 108;
            function = 9;
            payload = WriteEmptyOffers();
        }
        else if (kind == "likes")
        {
            service = 106;
            function = 11;
            payload = request.ContainsKey("ids") ? WriteUIntList(request["ids"]) : WriteEmptyList();
        }
        else if (kind == "rpc_ok")
        {
            service = Convert.ToByte(request["service"]);
            function = Convert.ToByte(request["function"]);
            payload = new byte[0];
        }
        else if (kind == "rpc_fail")
        {
            service = Convert.ToByte(request["service"]);
            function = Convert.ToByte(request["function"]);
            status = 255;
            payload = FailString(Convert.ToString(request["text"]));
        }
        else if (kind == "rpc_type")
        {
            service = Convert.ToByte(request["service"]);
            function = Convert.ToByte(request["function"]);
            payload = WriteFilled(Convert.ToString(request["type"]), Fields(request));
        }
        else if (kind == "rpc_list")
        {
            service = Convert.ToByte(request["service"]);
            function = Convert.ToByte(request["function"]);
            payload = WriteHexList(request.ContainsKey("items") ? request["items"] : null);
        }
        else if (kind == "rpc_uints")
        {
            service = Convert.ToByte(request["service"]);
            function = Convert.ToByte(request["function"]);
            payload = WriteUIntList(request.ContainsKey("ids") ? request["ids"] : null);
        }
        else
            throw new InvalidOperationException("unknown reply kind " + kind);
        return new Dictionary<string, object> { { "ok", true }, { "hex", ToHex(Frame(service, function, rpc, status, payload)) } };
    }

    static Dictionary<string, object> Push(Dictionary<string, object> request)
    {
        string kind = Convert.ToString(request["kind"]);
        byte[] frame;
        if (kind == "daily_bonus")
        {
            bool needAccept = request.ContainsKey("needAccept") && Convert.ToBoolean(request["needAccept"]);
            int day = request.ContainsKey("day") ? Convert.ToInt32(request["day"]) : 1;
            float timeLeft = request.ContainsKey("timeLeft") ? Convert.ToSingle(request["timeLeft"]) : 86400f;
            frame = ServerPush(109, 0, WriteDailyBonus(false, day, 0, needAccept, timeLeft));
        }
        else if (kind == "session")
        {
            Dictionary<string, object> fields = Fields(request);
            object inventory = request.ContainsKey("inventory") ? request["inventory"] : null;
            byte[] player = ServerPush(109, 2, WritePlayerStats(fields, inventory));
            if (fields.ContainsKey("RiderEquipment"))
                fields.Remove("RiderEquipment");
            byte[] horse = ServerPush(109, 3, WriteFilled("Protocol2.HorseStats", fields));
            frame = new byte[player.Length + horse.Length];
            Buffer.BlockCopy(player, 0, frame, 0, player.Length);
            Buffer.BlockCopy(horse, 0, frame, player.Length, horse.Length);
        }
        else if (kind == "cards")
            frame = CardsInit();
        else if (kind == "object")
            frame = ServerPush(Convert.ToByte(request["service"]), Convert.ToByte(request["function"]), WriteFilled(Convert.ToString(request["type"]), Fields(request)));
        else
            throw new InvalidOperationException("unknown push kind " + kind);
        return new Dictionary<string, object> { { "ok", true }, { "hex", ToHex(frame) } };
    }

    static byte[] WriteDailyBonus(bool isPremium, int day, int rewardIndex, bool needAccept, float timeLeft)
    {
        Type bonusType = Game.GetType("Protocol2.DailyBonus");
        object bonus = Activator.CreateInstance(bonusType);
        bonusType.GetProperty("IsPremium").SetValue(bonus, isPremium, null);
        bonusType.GetProperty("Day").SetValue(bonus, day, null);
        bonusType.GetProperty("RewardIndex").SetValue(bonus, rewardIndex, null);
        bonusType.GetProperty("NeedAccept").SetValue(bonus, needAccept, null);
        bonusType.GetProperty("TimeLeft").SetValue(bonus, timeLeft, null);
        var body = new MemoryStream();
        var writer = new BinaryWriter(body);
        bonusType.GetMethod("Write").Invoke(bonus, new object[] { writer });
        writer.Flush();
        return body.ToArray();
    }

    static byte[] ServerPush(byte service, byte function, byte[] payload)
    {
        var body = new MemoryStream();
        var writer = new BinaryWriter(body);
        writer.Write(service);
        writer.Write(function);
        if (payload != null && payload.Length > 0)
            writer.Write(payload);
        writer.Flush();
        byte[] raw = body.ToArray();
        var outer = new MemoryStream();
        var outerWriter = new BinaryWriter(outer);
        Call("Igor.Write", "Size", outerWriter, raw.Length);
        outerWriter.Write(raw);
        outerWriter.Flush();
        return outer.ToArray();
    }

    static byte[] WriteLogin(uint playerId, int userState, int access)
    {
        Type replyType = Game.GetType("Protocol2.ReplyLogin");
        object reply = Activator.CreateInstance(replyType);
        replyType.GetProperty("PlayerId").SetValue(reply, playerId, null);
        replyType.GetProperty("UserState").SetValue(reply, Enum.ToObject(Game.GetType("Protocol2.UserState"), userState), null);
        replyType.GetProperty("Status").SetValue(reply, Enum.ToObject(Game.GetType("Protocol2.Access"), access), null);
        var body = new MemoryStream();
        var writer = new BinaryWriter(body);
        replyType.GetMethod("Write").Invoke(reply, new object[] { writer });
        writer.Flush();
        return body.ToArray();
    }

    static byte[] WriteEmptyOffers()
    {
        Type offerType = Game.GetType("Protocol2.ReplySpecialOffers");
        object offer = Activator.CreateInstance(offerType);
        var body = new MemoryStream();
        var writer = new BinaryWriter(body);
        offerType.GetMethod("Write").Invoke(offer, new object[] { writer });
        writer.Flush();
        return body.ToArray();
    }

    static byte[] WriteEmptyList()
    {
        var body = new MemoryStream();
        var writer = new BinaryWriter(body);
        Call("Igor.Write", "Size", writer, 0);
        writer.Flush();
        return body.ToArray();
    }

    static byte[] Frame(byte service, byte function, ushort rpc, byte status, byte[] payload)
    {
        var body = new MemoryStream();
        var writer = new BinaryWriter(body);
        writer.Write(service);
        writer.Write(function);
        writer.Write(rpc);
        writer.Write(status);
        if (payload != null && payload.Length > 0)
            writer.Write(payload);
        writer.Flush();
        byte[] raw = body.ToArray();
        var outer = new MemoryStream();
        var outerWriter = new BinaryWriter(outer);
        Call("Igor.Write", "Size", outerWriter, raw.Length);
        outerWriter.Write(raw);
        outerWriter.Flush();
        return outer.ToArray();
    }

    static object Call(string typeName, string method, params object[] args)
    {
        Type type = Game.GetType(typeName);
        foreach (MethodInfo candidate in type.GetMethods(BindingFlags.Public | BindingFlags.Static))
        {
            if (candidate.Name != method || candidate.IsGenericMethod)
                continue;
            if (candidate.GetParameters().Length != args.Length)
                continue;
            return candidate.Invoke(null, args);
        }
        throw new MissingMethodException(typeName + "." + method);
    }

    static byte[] ParseHex(string hex)
    {
        if (string.IsNullOrEmpty(hex))
            return new byte[0];
        if ((hex.Length & 1) != 0)
            throw new FormatException("odd hex length");
        var data = new byte[hex.Length / 2];
        for (int i = 0; i < data.Length; i++)
            data[i] = Convert.ToByte(hex.Substring(i * 2, 2), 16);
        return data;
    }

    static string ToHex(byte[] data)
    {
        var builder = new StringBuilder(data.Length * 2);
        for (int i = 0; i < data.Length; i++)
            builder.Append(data[i].ToString("x2"));
        return builder.ToString();
    }

    static void LoadCatalog()
    {
        string path = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "protocol_map.json");
        if (!File.Exists(path))
            return;
        var raw = (Dictionary<string, object>)Json.DeserializeObject(File.ReadAllText(path));
        foreach (string key in raw.Keys)
        {
            var item = (Dictionary<string, object>)raw[key];
            Catalog[Convert.ToInt32(key)] = new CallInfo
            {
                Name = Convert.ToString(item["name"]),
                HasRpc = Convert.ToBoolean(item["hasRpc"]),
                EmptyReply = Convert.ToBoolean(item["emptyReply"]),
                Placeholder = item.ContainsKey("placeholder") ? Convert.ToString(item["placeholder"]) : "",
                PayloadKind = item.ContainsKey("payloadKind") ? Convert.ToString(item["payloadKind"]) : "none",
                PayloadType = item.ContainsKey("payloadType") ? Convert.ToString(item["payloadType"]) : ""
            };
        }
    }

    static void ReadAvatar(BinaryReader reader, Dictionary<string, object> message)
    {
        Type requestType = Game.GetType("Protocol2.RequestApplyAvatar");
        object request = Activator.CreateInstance(requestType);
        requestType.GetMethod("Read").Invoke(request, new object[] { reader });
        message["horseName"] = Convert.ToString(requestType.GetProperty("HorseName").GetValue(request, null));
        message["horseGender"] = Convert.ToInt32(requestType.GetProperty("HorseGender").GetValue(request, null));
        message["playerGender"] = Convert.ToInt32(requestType.GetProperty("PlayerGender").GetValue(request, null));
        message["playerSkin"] = Convert.ToUInt32(requestType.GetProperty("PlayerSkinId").GetValue(request, null));
        message["horseSkin"] = Convert.ToUInt32(requestType.GetProperty("HorseSkinId").GetValue(request, null));
        message["tailSkin"] = Convert.ToUInt32(requestType.GetProperty("HorseTailSkinId").GetValue(request, null));
    }

    static Dictionary<string, object> Fields(Dictionary<string, object> request)
    {
        if (!request.ContainsKey("fields") || request["fields"] == null)
            return new Dictionary<string, object>();
        return (Dictionary<string, object>)request["fields"];
    }

    static Dictionary<string, object> RoundTrip(Dictionary<string, object> request)
    {
        string typeName = Convert.ToString(request["type"]);
        byte[] written = WriteFilled(typeName, Fields(request));
        Type type = Game.GetType(typeName);
        object value = Activator.CreateInstance(type);
        using (var stream = new MemoryStream(written))
        using (var reader = new BinaryReader(stream))
            type.GetMethod("Read").Invoke(value, new object[] { reader });
        var seen = new Dictionary<string, object>();
        foreach (string name in new string[] { "Name", "Coins", "ActionPoints", "Day" })
        {
            object current = value;
            string[] parts = name.Split('.');
            PropertyInfo property = null;
            foreach (string part in parts)
            {
                property = current.GetType().GetProperty(part);
                if (property == null)
                    break;
                current = property.GetValue(current, null);
                if (current == null)
                    break;
            }
            if (property != null && current != null)
                seen[name] = current.ToString();
        }
        if (type.GetProperty("Money") != null)
        {
            object money = type.GetProperty("Money").GetValue(value, null);
            if (money != null)
                seen["Coins"] = money.GetType().GetProperty("Coins").GetValue(money, null).ToString();
        }
        return new Dictionary<string, object> { { "ok", true }, { "hex", ToHex(written) }, { "values", seen } };
    }

    static byte[] WritePlayerStats(Dictionary<string, object> fields, object inventory)
    {
        Type type = Game.GetType("Protocol2.PlayerStats");
        object value = Activator.CreateInstance(type);
        Fill(value, 0);
        Dictionary<string, object> playerFields = new Dictionary<string, object>(fields);
        object riderEquipment = null;
        if (playerFields.ContainsKey("RiderEquipment"))
        {
            riderEquipment = playerFields["RiderEquipment"];
            playerFields.Remove("RiderEquipment");
        }
        playerFields.Remove("Equipment");
        ApplyFields(value, playerFields);
        if (riderEquipment != null)
        {
            var worn = new Dictionary<string, object>();
            worn["Equipment"] = riderEquipment;
            ApplyFields(value, worn);
        }
        IList rows = inventory as IList;
        if (rows != null)
        {
            Type containerType = Game.GetType("Protocol2.ContainerItem");
            Type gameItemType = Game.GetType("Protocol2.GameItem");
            Type keyType = Game.GetType("Key");
            object list = Activator.CreateInstance(type.GetProperty("Inventory").PropertyType);
            MethodInfo add = list.GetType().GetMethod("Add");
            foreach (object entry in rows)
            {
                var row = (Dictionary<string, object>)entry;
                object game = Activator.CreateInstance(gameItemType);
                object key = keyType.GetMethod("FromHash").Invoke(null, new object[] { Convert.ToUInt32(row["hash"]) });
                gameItemType.GetProperty("ItemKey").SetValue(game, key, null);
                gameItemType.GetProperty("Level").SetValue(game, 1, null);
                object container = Activator.CreateInstance(containerType);
                containerType.GetProperty("GameItem").SetValue(container, game, null);
                containerType.GetProperty("Count").SetValue(container, ConvertTo(containerType.GetProperty("Count").PropertyType, row["count"]), null);
                add.Invoke(list, new object[] { container });
            }
            type.GetProperty("Inventory").SetValue(value, list, null);
        }
        return InvokeWrite(value);
    }

    static byte[] WriteFilled(string typeName, Dictionary<string, object> fields)
    {
        Type type = Game.GetType(typeName);
        if (type == null)
            throw new InvalidOperationException("missing type " + typeName);
        object value = Activator.CreateInstance(type);
        Fill(value, 0);
        ApplyFields(value, fields);
        return InvokeWrite(value);
    }

    static byte[] InvokeWrite(object value)
    {
        for (int attempt = 0; attempt < 16; attempt++)
        {
            try
            {
                var body = new MemoryStream();
                var writer = new BinaryWriter(body);
                value.GetType().GetMethod("Write").Invoke(value, new object[] { writer });
                writer.Flush();
                return body.ToArray();
            }
            catch (TargetInvocationException ex)
            {
                Exception inner = ex.InnerException ?? ex;
                string text = inner.Message ?? "";
                const string prefix = "Required property ";
                if (!text.StartsWith(prefix) || text.IndexOf(" is null") < 0)
                    throw inner;
                string prop = text.Substring(prefix.Length, text.IndexOf(" is null") - prefix.Length);
                PropertyInfo property = value.GetType().GetProperty(prop);
                if (property == null)
                    throw inner;
                object made = MakeValue(property.PropertyType, prop);
                if (made == null)
                    throw inner;
                property.SetValue(value, made, null);
                Fill(made, 1);
            }
        }
        throw new InvalidOperationException("could not fill " + value.GetType().FullName);
    }

    static void Fill(object value, int depth)
    {
        if (value == null || depth > 4)
            return;
        Type type = value.GetType();
        if (type.IsValueType || type == typeof(string))
            return;
        foreach (PropertyInfo property in type.GetProperties(BindingFlags.Public | BindingFlags.Instance))
        {
            if (!property.CanRead || !property.CanWrite || property.GetIndexParameters().Length > 0)
                continue;
            object current;
            try
            {
                current = property.GetValue(value, null);
            }
            catch (Exception)
            {
                continue;
            }
            if (current != null)
            {
                Fill(current, depth + 1);
                continue;
            }
            object made = MakeValue(property.PropertyType, property.Name);
            if (made == null)
                continue;
            try
            {
                property.SetValue(value, made, null);
            }
            catch (Exception)
            {
                continue;
            }
            Fill(made, depth + 1);
        }
    }

    static object MakeValue(Type type, string name)
    {
        Type underlying = Nullable.GetUnderlyingType(type);
        if (underlying != null)
            return null;
        if (type == typeof(string))
        {
            if (name == "MaterialName")
                return null;
            return (name == "Id" || name.EndsWith("Name")) ? "local" : "";
        }
        if (type == typeof(byte[]))
            return new byte[0];
        if (type.IsArray)
            return Array.CreateInstance(type.GetElementType(), 0);
        if (type.IsGenericType && type.GetGenericTypeDefinition().Name.StartsWith("List"))
            return Activator.CreateInstance(type);
        if (type.IsValueType)
            return Activator.CreateInstance(type);
        ConstructorInfo ctor = type.GetConstructor(Type.EmptyTypes);
        if (ctor == null)
            return null;
        return ctor.Invoke(null);
    }

    static void ApplyFields(object value, Dictionary<string, object> fields)
    {
        if (value == null || fields == null)
            return;
        foreach (string key in fields.Keys)
        {
            PropertyInfo property = value.GetType().GetProperty(key);
            if (property == null || !property.CanWrite)
                continue;
            object raw = fields[key];
            Type keyType = Game.GetType("Key");
            if (property.PropertyType == keyType && !(raw is Dictionary<string, object>))
            {
                object keyValue = keyType.GetMethod("FromHash").Invoke(null, new object[] { Convert.ToUInt32(raw) });
                property.SetValue(value, keyValue, null);
                continue;
            }
            if (raw is Dictionary<string, object>)
            {
                object child = property.GetValue(value, null);
                if (child == null)
                    child = MakeValue(property.PropertyType, key);
                ApplyFields(child, (Dictionary<string, object>)raw);
                property.SetValue(value, child, null);
            }
            else if ((raw is System.Collections.ArrayList || raw is object[]) && property.PropertyType.IsGenericType)
            {
                object list = property.GetValue(value, null);
                if (list == null)
                    list = Activator.CreateInstance(property.PropertyType);
                Type itemType = property.PropertyType.GetGenericArguments()[0];
                list.GetType().GetMethod("Clear").Invoke(list, null);
                foreach (object entry in (System.Collections.IEnumerable)raw)
                {
                    object item = MakeValue(itemType, key);
                    if (entry is Dictionary<string, object>)
                        ApplyFields(item, (Dictionary<string, object>)entry);
                    list.GetType().GetMethod("Add").Invoke(list, new object[] { item });
                }
                property.SetValue(value, list, null);
            }
            else
                property.SetValue(value, ConvertTo(property.PropertyType, raw), null);
        }
    }

    static object ConvertTo(Type type, object raw)
    {
        Type underlying = Nullable.GetUnderlyingType(type);
        Type target = underlying ?? type;
        object converted;
        if (target.IsEnum)
            converted = Enum.ToObject(target, Convert.ToInt32(raw));
        else if (target == typeof(string))
            converted = Convert.ToString(raw);
        else if (target == typeof(bool))
            converted = Convert.ToBoolean(raw);
        else if (target == typeof(int))
            converted = Convert.ToInt32(raw);
        else if (target == typeof(uint))
            converted = Convert.ToUInt32(raw);
        else if (target == typeof(ushort))
            converted = Convert.ToUInt16(raw);
        else if (target == typeof(short))
            converted = Convert.ToInt16(raw);
        else if (target == typeof(byte))
            converted = Convert.ToByte(raw);
        else if (target == typeof(float))
            converted = Convert.ToSingle(raw);
        else if (target == typeof(ulong))
            converted = Convert.ToUInt64(raw);
        else
            converted = raw;
        if (underlying == null)
            return converted;
        return Activator.CreateInstance(type, new object[] { converted });
    }

    static byte[] FailString(string text)
    {
        var body = new MemoryStream();
        var writer = new BinaryWriter(body);
        writer.Write(text ?? "");
        writer.Flush();
        return body.ToArray();
    }

    static byte[] WriteUIntList(object ids)
    {
        var body = new MemoryStream();
        var writer = new BinaryWriter(body);
        var values = ids as IList;
        int count = values == null ? 0 : values.Count;
        Call("Igor.Write", "Size", writer, count);
        if (values != null)
        {
            foreach (object id in values)
                Call("Igor.Write", "UInt", writer, Convert.ToUInt32(id));
        }
        writer.Flush();
        return body.ToArray();
    }

    static byte[] WriteHexList(object items)
    {
        var body = new MemoryStream();
        var writer = new BinaryWriter(body);
        var values = items as IList;
        int count = values == null ? 0 : values.Count;
        Call("Igor.Write", "Size", writer, count);
        if (values != null)
        {
            foreach (object item in values)
                writer.Write(ParseHex(Convert.ToString(item)));
        }
        writer.Flush();
        return body.ToArray();
    }

    static void WriteCourseCard(BinaryWriter writer)
    {
        Type cardType = Game.GetType("Protocol2.Card");
        Type categoryType = Game.GetType("Protocol2.CardCategory");
        object card = cardType.GetMethod("Create").Invoke(null, new object[] { Enum.ToObject(categoryType, 2) });
        cardType.GetProperty("Id").SetValue(card, "meadow", null);
        card.GetType().GetProperty("Level").SetValue(card, 1, null);
        Type nameType = Game.GetType("Protocol2.LocalizedString");
        object name = Activator.CreateInstance(nameType);
        nameType.GetProperty("Text").SetValue(name, "Meadow", null);
        nameType.GetProperty("Data").SetValue(name, Activator.CreateInstance(nameType.GetProperty("Data").PropertyType), null);
        card.GetType().GetProperty("Name").SetValue(card, name, null);
        card.GetType().GetProperty("IconPath").SetValue(card, "forestscene", null);
        Fill(card, 0);
        object map = card.GetType().GetProperty("Map").GetValue(card, null);
        if (map != null)
        {
            map.GetType().GetProperty("Terrain").SetValue(map, Activator.CreateInstance(Game.GetType("Key"), new object[] { "paddock_terrain" }), null);
            AddMeadowJumps(map);
        }
        InvokeWrite(card);
        cardType.GetMethod("WriteVariant").Invoke(null, new object[] { writer, card });
    }

    static void AddMeadowJumps(object map)
    {
        map.GetType().GetProperty("MinTime").SetValue(map, 60, null);
        map.GetType().GetProperty("ObstacleHeight").SetValue(map, 110, null);
        PropertyInfo obstaclesProp = map.GetType().GetProperty("Obstacles");
        object obstacles = obstaclesProp.GetValue(map, null) ?? Activator.CreateInstance(obstaclesProp.PropertyType);
        PropertyInfo sequenceProp = map.GetType().GetProperty("ObstacleSequence");
        object sequence = sequenceProp.GetValue(map, null) ?? Activator.CreateInstance(sequenceProp.PropertyType);
        Type itemType = Game.GetType("Protocol2.CourseMapItem");
        Type posType = Game.GetType("Protocol2.WorldPosition");
        Type keyType = Game.GetType("Key");
        Assembly unity = Assembly.LoadFrom(Path.Combine(ManagedDir, "UnityEngine.dll"));
        Type vectorType = unity.GetType("UnityEngine.Vector3");
        Type quatType = unity.GetType("UnityEngine.Quaternion");
        MethodInfo addItem = obstacles.GetType().GetMethod("Add");
        MethodInfo addSeq = sequence.GetType().GetMethod("Add");
        string[] ids = new string[] { "jump_01_110_blue", "jump_01_120_blue", "jump_02_110_green" };
        float z = 15f;
        for (int i = 0; i < ids.Length; i++)
        {
            object item = Activator.CreateInstance(itemType);
            itemType.GetProperty("Key").SetValue(item, Activator.CreateInstance(keyType, new object[] { ids[i] }), null);
            object place = Activator.CreateInstance(posType);
            object position = Activator.CreateInstance(vectorType, new object[] { 0f, 0f, z });
            object rotation = quatType.GetProperty("identity").GetValue(null, null);
            posType.GetProperty("Position").SetValue(place, position, null);
            posType.GetProperty("Rotation").SetValue(place, rotation, null);
            itemType.GetProperty("Position").SetValue(item, place, null);
            addItem.Invoke(obstacles, new object[] { item });
            addSeq.Invoke(sequence, new object[] { (ushort)i });
            z += 20f;
        }
        obstaclesProp.SetValue(map, obstacles, null);
        sequenceProp.SetValue(map, sequence, null);
    }

    static void WriteTerrainCard(BinaryWriter writer)
    {
        Type cardType = Game.GetType("Protocol2.Card");
        Type categoryType = Game.GetType("Protocol2.CardCategory");
        object card = cardType.GetMethod("Create").Invoke(null, new object[] { Enum.ToObject(categoryType, 8) });
        cardType.GetProperty("Id").SetValue(card, "paddock_terrain", null);
        Type nameType = Game.GetType("Protocol2.LocalizedString");
        object name = Activator.CreateInstance(nameType);
        nameType.GetProperty("Text").SetValue(name, "Paddock", null);
        nameType.GetProperty("Data").SetValue(name, Activator.CreateInstance(nameType.GetProperty("Data").PropertyType), null);
        card.GetType().GetProperty("Name").SetValue(card, name, null);
        card.GetType().GetProperty("Prefab").SetValue(card, "PaddockTerrain", null);
        card.GetType().GetProperty("Icon").SetValue(card, "GUI/Common/Icons/paddockscene", null);
        card.GetType().GetProperty("Type").SetValue(card, Enum.ToObject(Game.GetType("Protocol2.CourseItemType"), 1), null);
        Fill(card, 0);
        InvokeWrite(card);
        cardType.GetMethod("WriteVariant").Invoke(null, new object[] { writer, card });
    }

    static void WriteCheckpointCard(BinaryWriter writer)
    {
        Type cardType = Game.GetType("Protocol2.Card");
        Type categoryType = Game.GetType("Protocol2.CardCategory");
        object card = cardType.GetMethod("Create").Invoke(null, new object[] { Enum.ToObject(categoryType, 8) });
        cardType.GetProperty("Id").SetValue(card, "agility_checkpoint", null);
        Type nameType = Game.GetType("Protocol2.LocalizedString");
        object name = Activator.CreateInstance(nameType);
        nameType.GetProperty("Text").SetValue(name, "Checkpoint", null);
        nameType.GetProperty("Data").SetValue(name, Activator.CreateInstance(nameType.GetProperty("Data").PropertyType), null);
        card.GetType().GetProperty("Name").SetValue(card, name, null);
        card.GetType().GetProperty("Prefab").SetValue(card, "Prefabs/Objects/Agility/checkpoint", null);
        card.GetType().GetProperty("Icon").SetValue(card, "GUI/Common/Icons/agility_checkpoint", null);
        card.GetType().GetProperty("Type").SetValue(card, Enum.ToObject(Game.GetType("Protocol2.CourseItemType"), 8), null);
        Fill(card, 0);
        InvokeWrite(card);
        cardType.GetMethod("WriteVariant").Invoke(null, new object[] { writer, card });
    }

    static void WriteJumpCard(BinaryWriter writer, string id, string label)
    {
        Type cardType = Game.GetType("Protocol2.Card");
        Type categoryType = Game.GetType("Protocol2.CardCategory");
        object card = cardType.GetMethod("Create").Invoke(null, new object[] { Enum.ToObject(categoryType, 8) });
        cardType.GetProperty("Id").SetValue(card, id, null);
        Type nameType = Game.GetType("Protocol2.LocalizedString");
        object name = Activator.CreateInstance(nameType);
        nameType.GetProperty("Text").SetValue(name, label, null);
        nameType.GetProperty("Data").SetValue(name, Activator.CreateInstance(nameType.GetProperty("Data").PropertyType), null);
        card.GetType().GetProperty("Name").SetValue(card, name, null);
        card.GetType().GetProperty("Prefab").SetValue(card, "Prefabs/Objects/Obstacles/Generated/" + id, null);
        card.GetType().GetProperty("Icon").SetValue(card, "GUI/courseedit/icons/" + id, null);
        card.GetType().GetProperty("Shop").SetValue(card, true, null);
        card.GetType().GetProperty("Group").SetValue(card, "jumps", null);
        card.GetType().GetProperty("Type").SetValue(card, Enum.ToObject(Game.GetType("Protocol2.CourseItemType"), 2), null);
        Fill(card, 0);
        InvokeWrite(card);
        cardType.GetMethod("WriteVariant").Invoke(null, new object[] { writer, card });
    }

    static void WriteLocalesCard(BinaryWriter writer)
    {
        Type cardType = Game.GetType("Protocol2.Card");
        Type categoryType = Game.GetType("Protocol2.CardCategory");
        object card = cardType.GetMethod("Create").Invoke(null, new object[] { Enum.ToObject(categoryType, 10) });
        cardType.GetProperty("Id").SetValue(card, "locales", null);
        Fill(card, 0);
        object list = card.GetType().GetProperty("Locales").GetValue(card, null);
        Type localeType = Game.GetType("Protocol2.FacebookLocale");
        string[] codes = new string[] { "global", "en" };
        string[] names = new string[] { "Global", "English" };
        for (int i = 0; i < codes.Length; i++)
        {
            object locale = Activator.CreateInstance(localeType);
            localeType.GetProperty("Code").SetValue(locale, codes[i], null);
            localeType.GetProperty("Name").SetValue(locale, names[i], null);
            list.GetType().GetMethod("Add").Invoke(list, new object[] { locale });
        }
        InvokeWrite(card);
        cardType.GetMethod("WriteVariant").Invoke(null, new object[] { writer, card });
    }

    static void WriteTutorialMission(BinaryWriter writer)
    {
        Type cardType = Game.GetType("Protocol2.Card");
        Type categoryType = Game.GetType("Protocol2.CardCategory");
        object card = cardType.GetMethod("Create").Invoke(null, new object[] { Enum.ToObject(categoryType, 12) });
        cardType.GetProperty("Id").SetValue(card, "mission_start_run", null);
        Type nameType = Game.GetType("Protocol2.LocalizedString");
        object title = Activator.CreateInstance(nameType);
        nameType.GetProperty("Text").SetValue(title, "Start moving", null);
        nameType.GetProperty("Data").SetValue(title, Activator.CreateInstance(nameType.GetProperty("Data").PropertyType), null);
        object description = Activator.CreateInstance(nameType);
        nameType.GetProperty("Text").SetValue(description, "Move the horse to begin", null);
        nameType.GetProperty("Data").SetValue(description, Activator.CreateInstance(nameType.GetProperty("Data").PropertyType), null);
        card.GetType().GetProperty("Name").SetValue(card, title, null);
        card.GetType().GetProperty("Description").SetValue(card, description, null);
        card.GetType().GetProperty("IconPath").SetValue(card, "run", null);
        Fill(card, 0);
        object objective = card.GetType().GetProperty("Objective").GetValue(card, null);
        if (objective != null)
            objective.GetType().GetProperty("StartRun").SetValue(objective, ConvertTo(objective.GetType().GetProperty("StartRun").PropertyType, true), null);
        InvokeWrite(card);
        cardType.GetMethod("WriteVariant").Invoke(null, new object[] { writer, card });
    }

    static void WriteDailyReward(BinaryWriter writer, string id, int coins, int premiumCoins)
    {
        Type cardType = Game.GetType("Protocol2.Card");
        Type categoryType = Game.GetType("Protocol2.CardCategory");
        object card = cardType.GetMethod("Create").Invoke(null, new object[] { Enum.ToObject(categoryType, 14) });
        cardType.GetProperty("Id").SetValue(card, id, null);
        card.GetType().GetProperty("Bonus").SetValue(card, MakeReward(coins), null);
        card.GetType().GetProperty("PremiumBonus").SetValue(card, MakeReward(premiumCoins), null);
        InvokeWrite(card);
        cardType.GetMethod("WriteVariant").Invoke(null, new object[] { writer, card });
    }

    static object MakeReward(int coins)
    {
        Type rewardType = Game.GetType("Protocol2.Reward");
        object reward = Activator.CreateInstance(rewardType);
        Type moneyType = Game.GetType("Protocol2.Money");
        object money = Activator.CreateInstance(moneyType);
        moneyType.GetProperty("Coins").SetValue(money, coins, null);
        moneyType.GetProperty("SkillTickets").SetValue(money, 0, null);
        rewardType.GetProperty("Money").SetValue(reward, money, null);
        rewardType.GetProperty("Items").SetValue(reward, Activator.CreateInstance(rewardType.GetProperty("Items").PropertyType), null);
        return reward;
    }

    static void WriteItemCard(BinaryWriter writer, string id, string label, string descriptionText, int priceCoins, int rewardXp, int gearSlot = 0, int playerSlot = 0, int mesh = 0, string materialOverride = null, string iconOverride = null)
    {
        Type cardType = Game.GetType("Protocol2.Card");
        Type categoryType = Game.GetType("Protocol2.CardCategory");
        object card = cardType.GetMethod("Create").Invoke(null, new object[] { Enum.ToObject(categoryType, 1) });
        cardType.GetProperty("Id").SetValue(card, id, null);
        Type nameType = Game.GetType("Protocol2.LocalizedString");
        object title = Activator.CreateInstance(nameType);
        nameType.GetProperty("Text").SetValue(title, label, null);
        nameType.GetProperty("Data").SetValue(title, Activator.CreateInstance(nameType.GetProperty("Data").PropertyType), null);
        object description = Activator.CreateInstance(nameType);
        nameType.GetProperty("Text").SetValue(description, descriptionText, null);
        nameType.GetProperty("Data").SetValue(description, Activator.CreateInstance(nameType.GetProperty("Data").PropertyType), null);
        card.GetType().GetProperty("Name").SetValue(card, title, null);
        card.GetType().GetProperty("Description").SetValue(card, description, null);
        string icon = string.IsNullOrEmpty(iconOverride) ? id : iconOverride;
        if (playerSlot > 0 && id.StartsWith("rider_") && string.IsNullOrEmpty(iconOverride))
            icon = id.Substring("rider_".Length);
        card.GetType().GetProperty("IconPath").SetValue(card, icon, null);
        card.GetType().GetProperty("TexPath").SetValue(card, icon, null);
        int itemType = 1;
        if (gearSlot > 0)
            itemType = 3;
        else if (playerSlot > 0)
            itemType = 2;
        card.GetType().GetProperty("Type").SetValue(card, Enum.ToObject(Game.GetType("Protocol2.ItemType"), itemType), null);
        if (playerSlot > 0)
        {
            PropertyInfo riderType = card.GetType().GetProperty("PlayerEquipmentType");
            riderType.SetValue(card, ConvertTo(riderType.PropertyType, playerSlot), null);
        }
        if (gearSlot > 0)
        {
            PropertyInfo gearType = card.GetType().GetProperty("HorseEquipmentType");
            gearType.SetValue(card, ConvertTo(gearType.PropertyType, gearSlot), null);
            string tex = id.StartsWith("horse_") ? id.Substring("horse_".Length) : id;
            card.GetType().GetProperty("TexPath").SetValue(card, tex, null);
        }
        string materialName = null;
        if (gearSlot == 1)
            materialName = "horse_headgear";
        else if (gearSlot == 2)
            materialName = "horse_saddle";
        else if (gearSlot == 3)
            materialName = "horse_blanket1";
        else if (gearSlot == 4)
            materialName = "horse_ankleboots1";
        if (!string.IsNullOrEmpty(materialOverride))
            materialName = materialOverride;
        card.GetType().GetProperty("MaterialName").SetValue(card, materialName, null);
        card.GetType().GetProperty("MeshIndex").SetValue(card, mesh, null);
        Fill(card, 0);
        object price = card.GetType().GetProperty("Price").GetValue(card, null);
        if (price != null)
            price.GetType().GetProperty("Coins").SetValue(price, priceCoins, null);
        object reward = card.GetType().GetProperty("Reward").GetValue(card, null);
        if (reward != null)
        {
            reward.GetType().GetProperty("Xp").SetValue(reward, rewardXp, null);
            object money = reward.GetType().GetProperty("Money").GetValue(reward, null);
            if (money != null)
                money.GetType().GetProperty("Coins").SetValue(money, 0, null);
        }
        InvokeWrite(card);
        cardType.GetMethod("WriteVariant").Invoke(null, new object[] { writer, card });
    }

    static void WriteLootCard(BinaryWriter writer, string id, int color, string prefab)
    {
        Type cardType = Game.GetType("Protocol2.Card");
        Type categoryType = Game.GetType("Protocol2.CardCategory");
        object card = cardType.GetMethod("Create").Invoke(null, new object[] { Enum.ToObject(categoryType, 11) });
        cardType.GetProperty("Id").SetValue(card, id, null);
        Type nameType = Game.GetType("Protocol2.LocalizedString");
        object name = Activator.CreateInstance(nameType);
        nameType.GetProperty("Text").SetValue(name, id, null);
        nameType.GetProperty("Data").SetValue(name, Activator.CreateInstance(nameType.GetProperty("Data").PropertyType), null);
        card.GetType().GetProperty("Name").SetValue(card, name, null);
        card.GetType().GetProperty("Color").SetValue(card, Enum.ToObject(Game.GetType("Protocol2.LootColor"), color), null);
        card.GetType().GetProperty("Type").SetValue(card, Enum.ToObject(Game.GetType("Protocol2.LootType"), 2), null);
        card.GetType().GetProperty("PrefabName").SetValue(card, prefab, null);
        card.GetType().GetProperty("Icon").SetValue(card, "GUI/unknown", null);
        card.GetType().GetProperty("LootItems").SetValue(card, Activator.CreateInstance(card.GetType().GetProperty("LootItems").PropertyType), null);
        InvokeWrite(card);
        cardType.GetMethod("WriteVariant").Invoke(null, new object[] { writer, card });
    }

    static void AddShowdownPrizes(object card)
    {
        PropertyInfo listProperty = card.GetType().GetProperty("FinalRewards");
        object list = listProperty.GetValue(card, null);
        if (list == null)
            list = Activator.CreateInstance(listProperty.PropertyType);
        else
            list.GetType().GetMethod("Clear").Invoke(list, null);
        Type entryType = Game.GetType("Protocol2.DsFinalReward");
        Type prizeType = Game.GetType("Protocol2.Reward");
        Type moneyType = Game.GetType("Protocol2.Money");
        int[] places = new int[] { 1, 2, 3 };
        int[] coins = new int[] { 100, 50, 25 };
        for (int i = 0; i < places.Length; i++)
        {
            object entry = Activator.CreateInstance(entryType);
            object prize = Activator.CreateInstance(prizeType);
            object money = Activator.CreateInstance(moneyType);
            moneyType.GetProperty("Coins").SetValue(money, coins[i], null);
            moneyType.GetProperty("SkillTickets").SetValue(money, 0, null);
            prizeType.GetProperty("Money").SetValue(prize, money, null);
            prizeType.GetProperty("Items").SetValue(prize, Activator.CreateInstance(prizeType.GetProperty("Items").PropertyType), null);
            entryType.GetProperty("Place").SetValue(entry, (uint)places[i], null);
            entryType.GetProperty("Reward").SetValue(entry, prize, null);
            list.GetType().GetMethod("Add").Invoke(list, new object[] { entry });
        }
        listProperty.SetValue(card, list, null);
    }

    static void WriteStringsCard(BinaryWriter writer)
    {
        Type cardType = Game.GetType("Protocol2.Card");
        Type categoryType = Game.GetType("Protocol2.CardCategory");
        object card = cardType.GetMethod("Create").Invoke(null, new object[] { Enum.ToObject(categoryType, 34) });
        cardType.GetProperty("Id").SetValue(card, "strings_extra", null);
        Type locType = Game.GetType("Protocol2.LocalizedString");
        Type entryType = Game.GetType("Protocol2.LocalizedEntry");
        Type localeType = Game.GetType("Protocol2.Locale");
        PropertyInfo stringsProperty = card.GetType().GetProperty("Strings");
        object strings = Activator.CreateInstance(stringsProperty.PropertyType);
        string path = DataFile("localization_extra.json");
        if (File.Exists(path))
        {
            var serializer = new JavaScriptSerializer();
            serializer.MaxJsonLength = int.MaxValue;
            var root = serializer.Deserialize<Dictionary<string, object>>(File.ReadAllText(path));
            foreach (string key in root.Keys)
            {
                var fields = root[key] as Dictionary<string, object>;
                if (fields == null || !fields.ContainsKey("text"))
                    continue;
                object localized = Activator.CreateInstance(locType);
                locType.GetProperty("Text").SetValue(localized, Convert.ToString(fields["text"]), null);
                object data = Activator.CreateInstance(locType.GetProperty("Data").PropertyType);
                var translations = fields.ContainsKey("data") ? fields["data"] as Dictionary<string, object> : null;
                if (translations != null && translations.ContainsKey("ru"))
                {
                    var russian = translations["ru"] as Dictionary<string, object>;
                    if (russian != null)
                    {
                        object entry = Activator.CreateInstance(entryType);
                        entryType.GetProperty("Original").SetValue(entry, Convert.ToString(russian.ContainsKey("original") ? russian["original"] : fields["text"]), null);
                        entryType.GetProperty("Translation").SetValue(entry, Convert.ToString(russian.ContainsKey("translation") ? russian["translation"] : ""), null);
                        data.GetType().GetMethod("Add").Invoke(data, new object[] { Enum.ToObject(localeType, 3), entry });
                    }
                }
                locType.GetProperty("Data").SetValue(localized, data, null);
                strings.GetType().GetMethod("Add").Invoke(strings, new object[] { key, localized });
            }
        }
        stringsProperty.SetValue(card, strings, null);
        InvokeWrite(card);
        cardType.GetMethod("WriteVariant").Invoke(null, new object[] { writer, card });
    }

    sealed class GearPiece
    {
        public string Id;
        public string Name;
        public string Description;
        public string Material;
        public int Price;
        public int Gear;
        public int Player;
        public int Mesh;
    }

    sealed class Wardrobe
    {
        public List<GearPiece> Items = new List<GearPiece>();
        public List<string> PlayerSkins = new List<string>();
        public List<string> HorseSkins = new List<string>();
        public List<object> Hair = new List<object>();
    }

    static Wardrobe LoadWardrobe()
    {
        var wardrobe = new Wardrobe();
        string path = DataFile("wardrobe.json");
        if (!File.Exists(path))
        {
            AddBuiltinGear(wardrobe);
            return wardrobe;
        }
        Json.MaxJsonLength = int.MaxValue;
        var root = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path));
        var rows = root.ContainsKey("items") ? root["items"] as System.Collections.IList : null;
        if (rows != null)
        {
            foreach (object row in rows)
            {
                var fields = row as Dictionary<string, object>;
                if (fields == null || !fields.ContainsKey("id"))
                    continue;
                wardrobe.Items.Add(new GearPiece
                {
                    Id = Convert.ToString(fields["id"]),
                    Name = Convert.ToString(fields.ContainsKey("name") ? fields["name"] : fields["id"]),
                    Description = Convert.ToString(fields.ContainsKey("description") ? fields["description"] : ""),
                    Material = fields.ContainsKey("material") && fields["material"] != null ? Convert.ToString(fields["material"]) : null,
                    Price = fields.ContainsKey("price") ? Convert.ToInt32(fields["price"]) : 20,
                    Gear = fields.ContainsKey("gear") ? Convert.ToInt32(fields["gear"]) : 0,
                    Player = fields.ContainsKey("player") ? Convert.ToInt32(fields["player"]) : 0,
                    Mesh = fields.ContainsKey("mesh") ? Convert.ToInt32(fields["mesh"]) : 0
                });
            }
        }
        ReadNames(root, "player_skins", wardrobe.PlayerSkins);
        ReadNames(root, "horse_skins", wardrobe.HorseSkins);
        if (root.ContainsKey("hair") && root["hair"] is System.Collections.IList)
        {
            foreach (object entry in (System.Collections.IList)root["hair"])
                wardrobe.Hair.Add(entry);
        }
        if (wardrobe.Items.Count == 0)
            AddBuiltinGear(wardrobe);
        return wardrobe;
    }

    static void ReadNames(Dictionary<string, object> root, string key, List<string> dest)
    {
        if (!root.ContainsKey(key) || !(root[key] is System.Collections.IList))
            return;
        foreach (object name in (System.Collections.IList)root[key])
            dest.Add(Convert.ToString(name));
    }

    static void AddBuiltinGear(Wardrobe wardrobe)
    {
        wardrobe.Items.Add(new GearPiece { Id = "horse_headgear_01", Name = "Bridle", Description = "A plain bridle", Price = 30, Gear = 1 });
        wardrobe.Items.Add(new GearPiece { Id = "horse_saddle_01", Name = "Saddle", Description = "A plain saddle", Price = 40, Gear = 2 });
        wardrobe.Items.Add(new GearPiece { Id = "horse_blanket_01", Name = "Blanket", Description = "A plain saddle blanket", Price = 20, Gear = 3 });
        wardrobe.Items.Add(new GearPiece { Id = "horse_ankleboots_01", Name = "Ankle boots", Description = "A plain set of ankle boots", Price = 20, Gear = 4 });
        wardrobe.Items.Add(new GearPiece { Id = "rider_helmet_01", Name = "Helmet", Description = "A plain helmet", Price = 25, Player = 1, Material = "rider_common_helmet" });
        wardrobe.Items.Add(new GearPiece { Id = "rider_blazer_01", Name = "Jacket", Description = "A plain riding jacket", Price = 35, Player = 2 });
        wardrobe.Items.Add(new GearPiece { Id = "rider_pants_01", Name = "Breeches", Description = "A plain pair of breeches", Price = 20, Player = 3 });
        wardrobe.Items.Add(new GearPiece { Id = "rider_boots_01", Name = "Boots", Description = "A plain pair of boots", Price = 25, Player = 4 });
    }

    static void ApplyWardrobeSkins(object card, Wardrobe wardrobe)
    {
        SetStringList(card, "PlayerSkins", wardrobe.PlayerSkins);
        SetStringList(card, "HorseSkins", wardrobe.HorseSkins);
        if (wardrobe.Hair.Count == 0)
            return;
        Assembly unity = Assembly.LoadFrom(Path.Combine(ManagedDir, "UnityEngine.dll"));
        Type colorType = unity.GetType("UnityEngine.Color");
        Type hairType = Game.GetType("Protocol2.HorseHairSkin");
        PropertyInfo listProperty = card.GetType().GetProperty("HorseHairSkins");
        object list = Activator.CreateInstance(listProperty.PropertyType);
        MethodInfo add = list.GetType().GetMethod("Add");
        foreach (object entry in wardrobe.Hair)
        {
            var fields = entry as Dictionary<string, object>;
            if (fields == null)
                continue;
            object hair = Activator.CreateInstance(hairType);
            hairType.GetProperty("Main").SetValue(hair, MakeColor(colorType, fields["main"]), null);
            hairType.GetProperty("Spec").SetValue(hair, MakeColor(colorType, fields["spec"]), null);
            add.Invoke(list, new object[] { hair });
        }
        listProperty.SetValue(card, list, null);
    }

    static object MakeColor(Type colorType, object raw)
    {
        var list = (System.Collections.IList)raw;
        return Activator.CreateInstance(colorType, new object[] {
            Convert.ToSingle(list[0]), Convert.ToSingle(list[1]), Convert.ToSingle(list[2]), 1f
        });
    }

    static void SetStringList(object card, string propertyName, List<string> values)
    {
        if (values == null || values.Count == 0)
            return;
        PropertyInfo property = card.GetType().GetProperty(propertyName);
        object list = Activator.CreateInstance(property.PropertyType);
        MethodInfo add = list.GetType().GetMethod("Add");
        foreach (string value in values)
            add.Invoke(list, new object[] { value });
        property.SetValue(card, list, null);
    }

    sealed class CoursePiece
    {
        public string Id;
        public string Name;
        public string Prefab;
        public string Icon;
        public string Group;
        public int Type;
        public int Height;
        public int Price;
        public bool Shop;
    }

    static string DataFile(string name)
    {
        string installed = Path.GetFullPath(Path.Combine(ManagedDir, "..", "..", "UserData", name));
        if (File.Exists(installed))
            return installed;
        string shipped = Path.GetFullPath(Path.Combine(ManagedDir, "..", "..", "ServerEmu", "gamedata", name));
        if (File.Exists(shipped))
            return shipped;
        return installed;
    }

    static List<CoursePiece> LoadCoursePieces()
    {
        var pieces = new List<CoursePiece>();
        string path = DataFile("course_items.json");
        if (!File.Exists(path))
        {
            AddBuiltinCoursePieces(pieces);
            return pieces;
        }
        Json.MaxJsonLength = int.MaxValue;
        var root = Json.Deserialize<Dictionary<string, object>>(File.ReadAllText(path));
        var rows = root.ContainsKey("items") ? root["items"] as System.Collections.IList : null;
        if (rows != null)
        {
            foreach (object row in rows)
            {
                var fields = row as Dictionary<string, object>;
                if (fields == null || !fields.ContainsKey("id"))
                    continue;
                pieces.Add(new CoursePiece
                {
                    Id = Convert.ToString(fields["id"]),
                    Name = Convert.ToString(fields.ContainsKey("name") ? fields["name"] : fields["id"]),
                    Prefab = Convert.ToString(fields["prefab"]),
                    Icon = Convert.ToString(fields["icon"]),
                    Group = fields.ContainsKey("group") && fields["group"] != null ? Convert.ToString(fields["group"]) : "",
                    Type = Convert.ToInt32(fields["type"]),
                    Height = fields.ContainsKey("height") && fields["height"] != null ? Convert.ToInt32(fields["height"]) : 0,
                    Shop = !fields.ContainsKey("shop") || Convert.ToBoolean(fields["shop"])
                });
            }
        }
        if (pieces.Count == 0)
            AddBuiltinCoursePieces(pieces);
        return pieces;
    }

    static void AddBuiltinCoursePieces(List<CoursePiece> pieces)
    {
        pieces.Add(new CoursePiece { Id = "paddock_terrain", Name = "Paddock", Prefab = "PaddockTerrain", Icon = "GUI/Common/Icons/paddockscene", Type = 1, Shop = true });
        pieces.Add(new CoursePiece { Id = "agility_checkpoint", Name = "Checkpoint", Prefab = "Prefabs/Objects/Agility/checkpoint", Icon = "GUI/Common/Icons/agility_checkpoint", Type = 8, Shop = true });
        pieces.Add(new CoursePiece { Id = "jump_01_110_blue", Name = "Blue jump 110", Prefab = "Prefabs/Objects/Obstacles/Generated/jump_01_110_blue", Icon = "GUI/courseedit/icons/jump_01_110_blue", Group = "jumps", Type = 2, Height = 110, Shop = true });
        pieces.Add(new CoursePiece { Id = "jump_01_120_blue", Name = "Blue jump 120", Prefab = "Prefabs/Objects/Obstacles/Generated/jump_01_120_blue", Icon = "GUI/courseedit/icons/jump_01_120_blue", Group = "jumps", Type = 2, Height = 120, Shop = true });
        pieces.Add(new CoursePiece { Id = "jump_02_110_green", Name = "Green jump 110", Prefab = "Prefabs/Objects/Obstacles/Generated/jump_02_110_green", Icon = "GUI/courseedit/icons/jump_02_110_green", Group = "jumps", Type = 2, Height = 110, Shop = true });
        pieces.Add(new CoursePiece { Id = "jump_02_120_green", Name = "Green jump 120", Prefab = "Prefabs/Objects/Obstacles/Generated/jump_02_120_green", Icon = "GUI/courseedit/icons/jump_02_120_green", Group = "jumps", Type = 2, Height = 120, Shop = true });
    }

    static void WriteCoursePiece(BinaryWriter writer, CoursePiece piece)
    {
        Type cardType = Game.GetType("Protocol2.Card");
        Type categoryType = Game.GetType("Protocol2.CardCategory");
        object card = cardType.GetMethod("Create").Invoke(null, new object[] { Enum.ToObject(categoryType, 8) });
        cardType.GetProperty("Id").SetValue(card, piece.Id, null);
        Type nameType = Game.GetType("Protocol2.LocalizedString");
        object name = Activator.CreateInstance(nameType);
        nameType.GetProperty("Text").SetValue(name, piece.Name, null);
        nameType.GetProperty("Data").SetValue(name, Activator.CreateInstance(nameType.GetProperty("Data").PropertyType), null);
        card.GetType().GetProperty("Name").SetValue(card, name, null);
        card.GetType().GetProperty("Prefab").SetValue(card, piece.Prefab, null);
        card.GetType().GetProperty("Icon").SetValue(card, piece.Icon, null);
        card.GetType().GetProperty("Type").SetValue(card, Enum.ToObject(Game.GetType("Protocol2.CourseItemType"), piece.Type), null);
        card.GetType().GetProperty("Shop").SetValue(card, piece.Shop, null);
        if (!string.IsNullOrEmpty(piece.Group))
            card.GetType().GetProperty("Group").SetValue(card, piece.Group, null);
        Fill(card, 0);
        object price = card.GetType().GetProperty("Price").GetValue(card, null);
        if (price != null)
            price.GetType().GetProperty("Coins").SetValue(price, piece.Price, null);
        if (piece.Height > 0)
        {
            PropertyInfo height = card.GetType().GetProperty("Height");
            height.SetValue(card, ConvertTo(height.PropertyType, piece.Height), null);
        }
        InvokeWrite(card);
        cardType.GetMethod("WriteVariant").Invoke(null, new object[] { writer, card });
    }

    static byte[] CardsInit()
    {
        string[] specs = new string[] {
            "15:logic_horse", "16:logic_tutorial", "17:skins", "18:logic_courses", "19:logic_items",
            "20:logic_payment", "21:logic_main", "22:logic_action_points", "23:logic_daily_login",
            "24:logic_start_stats", "25:logic_match", "26:logic_paddock", "27:logic_daily_showdown",
            "28:logic_course_editor", "29:logic_grooming", "30:logic_chat", "31:logic_steam",
            "32:logic_guild", "33:logic_news"
        };
        Type cardType = Game.GetType("Protocol2.Card");
        Type categoryType = Game.GetType("Protocol2.CardCategory");
        MethodInfo create = cardType.GetMethod("Create");
        MethodInfo writeVariant = cardType.GetMethod("WriteVariant");
        LoadBalance();
        int[] dailyCoins = BalInts(new int[] { 10, 15, 20, 25, 30, 35, 40 }, "daily", "coins");
        int[] premiumCoins = BalInts(new int[] { 20, 30, 40, 50, 60, 70, 80 }, "daily", "premium_coins");
        var body = new MemoryStream();
        var writer = new BinaryWriter(body);
        Wardrobe wardrobe = LoadWardrobe();
        List<CoursePiece> coursePieces = LoadCoursePieces();
        Call("Igor.Write", "Size", writer, specs.Length + 11 + dailyCoins.Length + wardrobe.Items.Count + coursePieces.Count);
        WriteLocalesCard(writer);
        WriteStringsCard(writer);
        WriteTutorialMission(writer);
        WriteItemCard(writer, "carrot", BalString("Carrot", "items", "carrot", "name"), BalString("A carrot for your horse", "items", "carrot", "description"), ItemPrice("carrot", BalInt(10, "treats", "carrot", "coins")), BalInt(5, "treats", "carrot", "xp"));
        WriteItemCard(writer, "clover", BalString("Clover", "items", "clover", "name"), BalString("A lucky clover", "items", "clover", "description"), ItemPrice("clover", BalInt(15, "treats", "clover", "coins")), BalInt(8, "treats", "clover", "xp"));
        WriteItemCard(writer, "coinbag", BalString("Coin bag", "items", "coinbag", "name"), BalString("A small bag of coins", "items", "coinbag", "description"), ItemPrice("coinbag", BalInt(25, "treats", "coinbag", "coins")), BalInt(0, "treats", "coinbag", "xp"));
        WriteItemCard(writer, "item_apple", BalString("Apple", "items", "item_apple", "name"), BalString("An apple", "items", "item_apple", "description"), ItemPrice("item_apple", 0), 0, 0, 0, 0, null, "icon_apple");
        WriteItemCard(writer, "item_apple_big", BalString("Big apple", "items", "item_apple_big", "name"), BalString("A big apple", "items", "item_apple_big", "description"), ItemPrice("item_apple_big", 0), 0, 0, 0, 0, null, "icon_apple_big");
        foreach (GearPiece piece in wardrobe.Items)
            WriteItemCard(writer, piece.Id, BalString(piece.Name, "items", piece.Id, "name"), BalString(piece.Description, "items", piece.Id, "description"), ItemPrice(piece.Id, piece.Price), 0, piece.Gear, piece.Player, piece.Mesh, piece.Material);
        foreach (CoursePiece piece in coursePieces)
        {
            piece.Name = BalString(piece.Name, "items", piece.Id, "name");
            piece.Price = ItemPrice(piece.Id, BalInt(0, "course_editor", "piece_coins"));
            WriteCoursePiece(writer, piece);
        }
        WriteCourseCard(writer);
        for (int day = 1; day <= dailyCoins.Length; day++)
        {
            int premium = day <= premiumCoins.Length ? premiumCoins[day - 1] : dailyCoins[day - 1];
            WriteDailyReward(writer, "daily_reward_" + day, dailyCoins[day - 1], premium);
        }
        WriteLootCard(writer, "loot_blue", 1, "present_blue");
        WriteLootCard(writer, "loot_yellow", 2, "present_yellow");
        foreach (string spec in specs)
        {
            string[] parts = spec.Split(':');
            object category = Enum.ToObject(categoryType, int.Parse(parts[0]));
            object card = create.Invoke(null, new object[] { category });
            cardType.GetProperty("Id").SetValue(card, parts[1], null);
            Fill(card, 0);
            if (parts[1] == "logic_action_points")
            {
                card.GetType().GetProperty("MaxValue").SetValue(card, (uint)BalInt(200, "action_points", "max"), null);
                card.GetType().GetProperty("PracticeReduce").SetValue(card, (uint)BalInt(20, "action_points", "practice"), null);
                card.GetType().GetProperty("RmReduce").SetValue(card, (uint)BalInt(20, "action_points", "ranked"), null);
            }
            if (parts[1] == "logic_main")
            {
                Type mainType = card.GetType();
                mainType.GetProperty("LadderTopSize").SetValue(card, 100, null);
                mainType.GetProperty("MaxBestScores").SetValue(card, 10, null);
                mainType.GetProperty("PlayerNameMaxSize").SetValue(card, 16, null);
                mainType.GetProperty("HorseNameMaxSize").SetValue(card, 16, null);
                mainType.GetProperty("SkillPointsForLevelUp").SetValue(card, 1f, null);
                SetPriceCoins(card, "ChangeAvatar", BalInt(4000, "avatar_change_coins"));
                PropertyInfo levels = mainType.GetProperty("LevelsXp");
                object list = levels.GetValue(card, null);
                if (list == null)
                    list = Activator.CreateInstance(levels.PropertyType);
                MethodInfo addXp = list.GetType().GetMethod("Add");
                foreach (int xp in new int[] { 100, 250, 500, 1000 })
                    addXp.Invoke(list, new object[] { xp });
                levels.SetValue(card, list, null);
            }
            if (parts[1] == "logic_course_editor")
            {
                SetPriceCoins(card, "SlotPrice", BalInt(0, "course_editor", "slot_coins"));
                SetPriceCoins(card, "SubmitPrice", BalInt(0, "course_editor", "submit_coins"));
            }
            if (parts[1] == "logic_grooming")
            {
                card.GetType().GetProperty("BuffTheshold").SetValue(card, BalFloat(0.8f, "grooming", "clean_at"), null);
                card.GetType().GetProperty("DebuffTheshold").SetValue(card, BalFloat(0.3f, "grooming", "dirty_at"), null);
                card.GetType().GetProperty("ReducePerMatch").SetValue(card, BalFloat(0.05f, "grooming", "wear_per_ride"), null);
                object price = card.GetType().GetProperty("BuyCost").GetValue(card, null);
                if (price != null)
                    price.GetType().GetProperty("Coins").SetValue(price, BalInt(20, "grooming", "full_groom_coins"), null);
            }
            if (parts[1] == "logic_guild")
            {
                Type guildType = card.GetType();
                guildType.GetProperty("GuildMaxSize").SetValue(card, BalInt(20, "club", "max_members"), null);
                guildType.GetProperty("GuildMaxNameSize").SetValue(card, BalInt(30, "club", "name_length"), null);
                guildType.GetProperty("GuildMaxNameShortSize").SetValue(card, BalInt(10, "club", "short_name_length"), null);
                guildType.GetProperty("GuildMaxDescriptionSize").SetValue(card, BalInt(400, "club", "description_length"), null);
                guildType.GetProperty("GuildMaxWelcomeSize").SetValue(card, BalInt(100, "club", "welcome_length"), null);
                var icons = new List<string>();
                icons.Add("GUI/ItemIcons/small/carrot");
                guildType.GetProperty("Icons").SetValue(card, icons, null);
            }
            if (parts[1] == "logic_tutorial")
            {
                PropertyInfo listProperty = card.GetType().GetProperty("Missions");
                object keys = Activator.CreateInstance(listProperty.PropertyType);
                keys.GetType().GetMethod("Add").Invoke(keys, new object[] { Activator.CreateInstance(Game.GetType("Key"), new object[] { "mission_start_run" }) });
                listProperty.SetValue(card, keys, null);
            }
            if (parts[1] == "logic_news")
            {
                PropertyInfo listProperty = card.GetType().GetProperty("News");
                object list = listProperty.GetValue(card, null);
                if (list == null)
                    list = Activator.CreateInstance(listProperty.PropertyType);
                Type newsType = Game.GetType("Protocol2.GameNews");
                Type nameType = Game.GetType("Protocol2.LocalizedString");
                object item = Activator.CreateInstance(newsType);
                newsType.GetProperty("Enabled").SetValue(item, true, null);
                object title = Activator.CreateInstance(nameType);
                nameType.GetProperty("Text").SetValue(title, "Meadow server", null);
                nameType.GetProperty("Data").SetValue(title, Activator.CreateInstance(nameType.GetProperty("Data").PropertyType), null);
                object newsBody = Activator.CreateInstance(nameType);
                nameType.GetProperty("Text").SetValue(newsBody, "Rides, the shop, and the club stay on this server.", null);
                nameType.GetProperty("Data").SetValue(newsBody, Activator.CreateInstance(nameType.GetProperty("Data").PropertyType), null);
                newsType.GetProperty("Capture").SetValue(item, title, null);
                newsType.GetProperty("Text").SetValue(item, newsBody, null);
                list.GetType().GetMethod("Add").Invoke(list, new object[] { item });
                listProperty.SetValue(card, list, null);
            }
            if (parts[1] == "logic_daily_login")
            {
                PropertyInfo listProperty = card.GetType().GetProperty("Days");
                object keys = Activator.CreateInstance(listProperty.PropertyType);
                MethodInfo add = keys.GetType().GetMethod("Add");
                for (int day = 1; day <= dailyCoins.Length; day++)
                    add.Invoke(keys, new object[] { Activator.CreateInstance(Game.GetType("Key"), new object[] { "daily_reward_" + day }) });
                listProperty.SetValue(card, keys, null);
            }
            if (parts[1] == "skins")
                ApplyWardrobeSkins(card, wardrobe);
            if (parts[1] == "logic_items")
            {
                var itemIds = new List<string>();
                itemIds.Add("carrot");
                itemIds.Add("clover");
                itemIds.Add("coinbag");
                foreach (GearPiece piece in wardrobe.Items)
                    itemIds.Add(piece.Id);
                foreach (string listName in new string[] { "AllItems", "ShopItems" })
                {
                    PropertyInfo listProperty = card.GetType().GetProperty(listName);
                    object keys = Activator.CreateInstance(listProperty.PropertyType);
                    MethodInfo add = keys.GetType().GetMethod("Add");
                    foreach (string itemId in itemIds)
                        add.Invoke(keys, new object[] { Activator.CreateInstance(Game.GetType("Key"), new object[] { itemId }) });
                    listProperty.SetValue(card, keys, null);
                }
            }
            if (parts[1] == "logic_courses")
            {
                foreach (string listName in new string[] { "AllCourses", "RmCourses", "DsCourses" })
                {
                    PropertyInfo listProperty = card.GetType().GetProperty(listName);
                    object keys = Activator.CreateInstance(listProperty.PropertyType);
                    keys.GetType().GetMethod("Add").Invoke(keys, new object[] { Activator.CreateInstance(Game.GetType("Key"), new object[] { "meadow" }) });
                    listProperty.SetValue(card, keys, null);
                }
            }
            if (parts[1] == "logic_daily_showdown")
                AddShowdownPrizes(card);
            if (parts[1] == "logic_paddock")
            {
                Type keyType = Game.GetType("Key");
                object list = Activator.CreateInstance(card.GetType().GetProperty("Loot").PropertyType);
                MethodInfo add = list.GetType().GetMethod("Add");
                add.Invoke(list, new object[] { Activator.CreateInstance(keyType, new object[] { "loot_blue" }) });
                add.Invoke(list, new object[] { Activator.CreateInstance(keyType, new object[] { "loot_yellow" }) });
                card.GetType().GetProperty("Loot").SetValue(card, list, null);
                card.GetType().GetProperty("YellowSpawnInterval").SetValue(card, 20f, null);
                card.GetType().GetProperty("BlueSpawnInterval").SetValue(card, 20f, null);
            }
            InvokeWrite(card);
            try
            {
                writeVariant.Invoke(null, new object[] { writer, card });
            }
            catch (TargetInvocationException ex)
            {
                throw new InvalidOperationException(parts[1] + ": " + (ex.InnerException == null ? ex.Message : ex.InnerException.Message), ex);
            }
        }
        writer.Flush();
        return ServerPush(101, 0, body.ToArray());
    }

    sealed class CallInfo
    {
        public string Name;
        public bool HasRpc;
        public bool EmptyReply;
        public string Placeholder;
        public string PayloadKind;
        public string PayloadType;
    }

    sealed class FrameBuffer
    {
        bool isParseSize;
        uint parseSize;
        int shiftIndex;
        readonly MemoryStream recv = new MemoryStream();

        public List<byte[]> Take(byte[] data)
        {
            var packets = new List<byte[]>();
            int i = 0;
            while (i < data.Length)
            {
                if (!isParseSize)
                {
                    uint num = data[i];
                    i++;
                    if ((num & 128U) != 0U)
                    {
                        parseSize |= (num & 127U) << shiftIndex;
                        shiftIndex += 7;
                        continue;
                    }
                    parseSize |= num << shiftIndex;
                    shiftIndex = 0;
                    if (parseSize == 0U)
                        throw new InvalidOperationException("TransportZeroPacket");
                    if (parseSize >= BodyMaxSize)
                        throw new InvalidOperationException("packet too large");
                    isParseSize = true;
                }
                uint available = (uint)(data.Length - i);
                uint take = Math.Min(parseSize, available);
                recv.Write(data, i, (int)take);
                i += (int)take;
                parseSize -= take;
                if (parseSize == 0U)
                {
                    isParseSize = false;
                    packets.Add(recv.ToArray());
                    recv.SetLength(0);
                    recv.Position = 0;
                }
            }
            return packets;
        }
    }
}
