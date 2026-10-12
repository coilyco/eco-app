using System.Collections;
using Newtonsoft.Json;
using Newtonsoft.Json.Serialization;

namespace EcoReplay;

// Serializes a GameAction (or any object) into a bounded, flat JSON snapshot
// for the replay viewer. The recorder must never let this balloon: a single
// click action (interact / place / trade / claim) carries wide references —
// inventories, chunk lists, world-object graphs, property graphs — that a
// naive Newtonsoft walk expands into gigabytes of intermediate strings before
// anything errors (issue #28, bug 1).
//
// The bound here is structural, not merely depth-limited. `MaxDepth` only
// constrains nesting and does nothing for serialization breadth, which is what
// actually blew up. Instead:
//
//   * Only allow-listed scalar-ish property *types* are serialized as values.
//   * Every other property is collapsed to a runtime summary string
//     ("<name>", "<n items>", or a type label) and never recursed into, so the
//     graph is flattened to a single level no matter how wide it is.
//   * A hard byte cap replaces the whole body with a stub if it still exceeds
//     16 KB after serialization.
//
// This type is deliberately free of Eco dependencies so it can be unit-tested
// against synthetic objects (see mods/replay/tests).
public static class BodySerializer
{
    // Bodies larger than this after serialization are discarded for a stub.
    public const int MaxBodyBytes = 16 * 1024;

    // Cap on how far we iterate a lazy (non-ICollection) enumerable purely to
    // count it. ICollection/array/dictionary report Count in O(1) and never
    // reach this; only lazy sequences are walked, and only up to the cap.
    private const int EnumerateCountCap = 1000;

    private static readonly JsonSerializerSettings Settings = new()
    {
        ReferenceLoopHandling = ReferenceLoopHandling.Ignore,
        NullValueHandling = NullValueHandling.Ignore,
        MaxDepth = 2,
        Error = (_, args) => args.ErrorContext.Handled = true,
        ContractResolver = new ShallowResolver(),
        Formatting = Formatting.None,
    };

    public static string Serialize(object? value)
    {
        if (value == null) return "{}";

        string body;
        try
        {
            body = JsonConvert.SerializeObject(value, Settings);
        }
        catch
        {
            return "{}";
        }

        if (System.Text.Encoding.UTF8.GetByteCount(body) > MaxBodyBytes)
        {
            body = JsonConvert.SerializeObject(new
            {
                truncated = true,
                action_type = value.GetType().Name,
            });
        }
        return body;
    }

    // True for types that can be serialized directly with no risk of pulling in
    // an entity graph: primitives, enums, and a short allow-list of value-ish
    // framework types. Everything else is summarized.
    internal static bool IsSafeScalar(Type type)
    {
        var t = Nullable.GetUnderlyingType(type) ?? type;
        if (t.IsEnum) return true;
        if (t.IsPrimitive) return true; // bool, char, byte..long, float, double, nint...
        return t == typeof(string)
            || t == typeof(decimal)
            || t == typeof(DateTime)
            || t == typeof(DateTimeOffset)
            || t == typeof(TimeSpan)
            || t == typeof(Guid);
    }

    // Cap on a struct's ToString, so a value type with a chatty override cannot
    // widen the body. Coordinates are a few dozen characters.
    internal const int MaxStructTextChars = 128;

    // A struct's own ToString text, or its type name when it only inherits
    // ValueType.ToString (which would print the full type name anyway).
    internal static string StructText(object value, Type type)
    {
        var toString = type.GetMethod("ToString", Type.EmptyTypes);
        if (toString == null || toString.DeclaringType != type) return type.Name;
        string? text;
        try
        {
            text = value.ToString();
        }
        catch
        {
            return type.Name;
        }
        if (string.IsNullOrEmpty(text)) return type.Name;
        return text.Length <= MaxStructTextChars ? text : text[..MaxStructTextChars];
    }

    // A bounded count summary for a collection. Never enumerates the elements
    // themselves — the whole point is to avoid materializing a wide graph.
    internal static string SummarizeEnumerable(IEnumerable items)
    {
        // List<>, arrays, HashSet<>, Dictionary<> and most Eco collections
        // implement the non-generic ICollection and report Count in O(1).
        if (items is ICollection c) return $"<{c.Count} items>";

        // A lazy sequence: count with a hard cap so a pathological or infinite
        // enumerable can't run away even just being counted.
        var n = 0;
        foreach (var _ in items)
        {
            if (++n > EnumerateCountCap) return $"<{EnumerateCountCap}+ items>";
        }
        return $"<{n} items>";
    }
}

// Contract resolver that collapses every non-scalar property to a runtime
// summary. Because a summarized property yields only a string (or a boxed
// scalar), Newtonsoft never recurses into an Eco entity graph — breadth and
// depth are both bounded structurally, regardless of declared or runtime type.
internal sealed class ShallowResolver : DefaultContractResolver
{
    protected override JsonProperty CreateProperty(
        System.Reflection.MemberInfo member, MemberSerialization memberSerialization)
    {
        var prop = base.CreateProperty(member, memberSerialization);
        var declared = prop.PropertyType;

        // A property whose *declared* type is a safe scalar can only ever hold a
        // safe scalar, so let it through untouched. Everything else — object,
        // interfaces (IAlias / IOwned / IEnumerable<T>), entities, collections —
        // is summarized at runtime against its actual value.
        if (declared == null || !BodySerializer.IsSafeScalar(declared))
        {
            prop.ValueProvider = new SummaryValueProvider(prop.ValueProvider!);

            // Retype the property to object so Newtonsoft resolves the contract
            // from the *runtime* value our provider returns (a summary string or
            // boxed scalar), not the declared type. This matters when the
            // declared type is sealed: Newtonsoft treats a sealed property
            // contract as final and skips the runtime re-resolution, which would
            // otherwise serialize our summary string against the sealed entity's
            // object contract and emit `{}` instead of the name. (issue #67)
            prop.PropertyType = typeof(object);
        }
        return prop;
    }
}

// Wraps a property's value provider so serialization sees a bounded summary
// instead of the live object: the scalar itself when the runtime value is
// scalar, a display name for a named entity, a count for a collection, or the
// type name as a last resort. It never returns a complex object, so the
// serializer cannot walk any further.
internal sealed class SummaryValueProvider : IValueProvider
{
    private readonly IValueProvider inner;
    public SummaryValueProvider(IValueProvider inner) => this.inner = inner;

    public object? GetValue(object target)
    {
        object? v;
        try
        {
            v = inner.GetValue(target);
        }
        catch
        {
            return null;
        }
        if (v == null) return null;

        var t = v.GetType();

        // A property declared `object`/interface may actually hold a scalar.
        if (BodySerializer.IsSafeScalar(t)) return v;

        // Collections: a bounded count summary, never the elements. (Checked
        // before the name lookup so an enumerable entity is still summarized.)
        if (v is IEnumerable en) return BodySerializer.SummarizeEnumerable(en);

        // A value type with its own ToString (Vector3i, Vector3) is a small,
        // copy-by-value coordinate rather than an entity graph, so its text is
        // the useful summary. Without this, positions collapsed to "Vector3i".
        if (t.IsValueType) return BodySerializer.StructText(v, t);

        // Named Eco entity (User, WorldObject, Deed, ...): its display name.
        try
        {
            var nameProp = t.GetProperty("Name");
            if (nameProp != null
                && nameProp.GetIndexParameters().Length == 0
                && nameProp.GetValue(v) is string s
                && s.Length > 0)
            {
                return s;
            }
        }
        catch
        {
            // Fall through to the type label below.
        }

        // Anything else: a stable type label, no recursion.
        return t.Name;
    }

    public void SetValue(object target, object? value) => inner.SetValue(target, value!);
}
