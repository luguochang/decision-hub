var R;
(function(i) {
  i.assertEqual = (u) => {
  };
  function t(u) {
  }
  i.assertIs = t;
  function n(u) {
    throw new Error();
  }
  i.assertNever = n, i.arrayToEnum = (u) => {
    const m = {};
    for (const _ of u)
      m[_] = _;
    return m;
  }, i.getValidEnumValues = (u) => {
    const m = i.objectKeys(u).filter((f) => typeof u[u[f]] != "number"), _ = {};
    for (const f of m)
      _[f] = u[f];
    return i.objectValues(_);
  }, i.objectValues = (u) => i.objectKeys(u).map(function(m) {
    return u[m];
  }), i.objectKeys = typeof Object.keys == "function" ? (u) => Object.keys(u) : (u) => {
    const m = [];
    for (const _ in u)
      Object.prototype.hasOwnProperty.call(u, _) && m.push(_);
    return m;
  }, i.find = (u, m) => {
    for (const _ of u)
      if (m(_))
        return _;
  }, i.isInteger = typeof Number.isInteger == "function" ? (u) => Number.isInteger(u) : (u) => typeof u == "number" && Number.isFinite(u) && Math.floor(u) === u;
  function d(u, m = " | ") {
    return u.map((_) => typeof _ == "string" ? `'${_}'` : _).join(m);
  }
  i.joinValues = d, i.jsonStringifyReplacer = (u, m) => typeof m == "bigint" ? m.toString() : m;
})(R || (R = {}));
var _e;
(function(i) {
  i.mergeShapes = (t, n) => ({
    ...t,
    ...n
    // second overwrites first
  });
})(_e || (_e = {}));
const v = R.arrayToEnum([
  "string",
  "nan",
  "number",
  "integer",
  "float",
  "boolean",
  "date",
  "bigint",
  "symbol",
  "function",
  "undefined",
  "null",
  "array",
  "object",
  "unknown",
  "promise",
  "void",
  "never",
  "map",
  "set"
]), I = (i) => {
  switch (typeof i) {
    case "undefined":
      return v.undefined;
    case "string":
      return v.string;
    case "number":
      return Number.isNaN(i) ? v.nan : v.number;
    case "boolean":
      return v.boolean;
    case "function":
      return v.function;
    case "bigint":
      return v.bigint;
    case "symbol":
      return v.symbol;
    case "object":
      return Array.isArray(i) ? v.array : i === null ? v.null : i.then && typeof i.then == "function" && i.catch && typeof i.catch == "function" ? v.promise : typeof Map < "u" && i instanceof Map ? v.map : typeof Set < "u" && i instanceof Set ? v.set : typeof Date < "u" && i instanceof Date ? v.date : v.object;
    default:
      return v.unknown;
  }
}, p = R.arrayToEnum([
  "invalid_type",
  "invalid_literal",
  "custom",
  "invalid_union",
  "invalid_union_discriminator",
  "invalid_enum_value",
  "unrecognized_keys",
  "invalid_arguments",
  "invalid_return_type",
  "invalid_date",
  "invalid_string",
  "too_small",
  "too_big",
  "invalid_intersection_types",
  "not_multiple_of",
  "not_finite"
]);
class z extends Error {
  get errors() {
    return this.issues;
  }
  constructor(t) {
    super(), this.issues = [], this.addIssue = (d) => {
      this.issues = [...this.issues, d];
    }, this.addIssues = (d = []) => {
      this.issues = [...this.issues, ...d];
    };
    const n = new.target.prototype;
    Object.setPrototypeOf ? Object.setPrototypeOf(this, n) : this.__proto__ = n, this.name = "ZodError", this.issues = t;
  }
  format(t) {
    const n = t || function(m) {
      return m.message;
    }, d = { _errors: [] }, u = (m) => {
      for (const _ of m.issues)
        if (_.code === "invalid_union")
          _.unionErrors.map(u);
        else if (_.code === "invalid_return_type")
          u(_.returnTypeError);
        else if (_.code === "invalid_arguments")
          u(_.argumentsError);
        else if (_.path.length === 0)
          d._errors.push(n(_));
        else {
          let f = d, x = 0;
          for (; x < _.path.length; ) {
            const k = _.path[x];
            x === _.path.length - 1 ? (f[k] = f[k] || { _errors: [] }, f[k]._errors.push(n(_))) : f[k] = f[k] || { _errors: [] }, f = f[k], x++;
          }
        }
    };
    return u(this), d;
  }
  static assert(t) {
    if (!(t instanceof z))
      throw new Error(`Not a ZodError: ${t}`);
  }
  toString() {
    return this.message;
  }
  get message() {
    return JSON.stringify(this.issues, R.jsonStringifyReplacer, 2);
  }
  get isEmpty() {
    return this.issues.length === 0;
  }
  flatten(t = (n) => n.message) {
    const n = {}, d = [];
    for (const u of this.issues)
      if (u.path.length > 0) {
        const m = u.path[0];
        n[m] = n[m] || [], n[m].push(t(u));
      } else
        d.push(t(u));
    return { formErrors: d, fieldErrors: n };
  }
  get formErrors() {
    return this.flatten();
  }
}
z.create = (i) => new z(i);
const se = (i, t) => {
  let n;
  switch (i.code) {
    case p.invalid_type:
      i.received === v.undefined ? n = "Required" : n = `Expected ${i.expected}, received ${i.received}`;
      break;
    case p.invalid_literal:
      n = `Invalid literal value, expected ${JSON.stringify(i.expected, R.jsonStringifyReplacer)}`;
      break;
    case p.unrecognized_keys:
      n = `Unrecognized key(s) in object: ${R.joinValues(i.keys, ", ")}`;
      break;
    case p.invalid_union:
      n = "Invalid input";
      break;
    case p.invalid_union_discriminator:
      n = `Invalid discriminator value. Expected ${R.joinValues(i.options)}`;
      break;
    case p.invalid_enum_value:
      n = `Invalid enum value. Expected ${R.joinValues(i.options)}, received '${i.received}'`;
      break;
    case p.invalid_arguments:
      n = "Invalid function arguments";
      break;
    case p.invalid_return_type:
      n = "Invalid function return type";
      break;
    case p.invalid_date:
      n = "Invalid date";
      break;
    case p.invalid_string:
      typeof i.validation == "object" ? "includes" in i.validation ? (n = `Invalid input: must include "${i.validation.includes}"`, typeof i.validation.position == "number" && (n = `${n} at one or more positions greater than or equal to ${i.validation.position}`)) : "startsWith" in i.validation ? n = `Invalid input: must start with "${i.validation.startsWith}"` : "endsWith" in i.validation ? n = `Invalid input: must end with "${i.validation.endsWith}"` : R.assertNever(i.validation) : i.validation !== "regex" ? n = `Invalid ${i.validation}` : n = "Invalid";
      break;
    case p.too_small:
      i.type === "array" ? n = `Array must contain ${i.exact ? "exactly" : i.inclusive ? "at least" : "more than"} ${i.minimum} element(s)` : i.type === "string" ? n = `String must contain ${i.exact ? "exactly" : i.inclusive ? "at least" : "over"} ${i.minimum} character(s)` : i.type === "number" ? n = `Number must be ${i.exact ? "exactly equal to " : i.inclusive ? "greater than or equal to " : "greater than "}${i.minimum}` : i.type === "bigint" ? n = `Number must be ${i.exact ? "exactly equal to " : i.inclusive ? "greater than or equal to " : "greater than "}${i.minimum}` : i.type === "date" ? n = `Date must be ${i.exact ? "exactly equal to " : i.inclusive ? "greater than or equal to " : "greater than "}${new Date(Number(i.minimum))}` : n = "Invalid input";
      break;
    case p.too_big:
      i.type === "array" ? n = `Array must contain ${i.exact ? "exactly" : i.inclusive ? "at most" : "less than"} ${i.maximum} element(s)` : i.type === "string" ? n = `String must contain ${i.exact ? "exactly" : i.inclusive ? "at most" : "under"} ${i.maximum} character(s)` : i.type === "number" ? n = `Number must be ${i.exact ? "exactly" : i.inclusive ? "less than or equal to" : "less than"} ${i.maximum}` : i.type === "bigint" ? n = `BigInt must be ${i.exact ? "exactly" : i.inclusive ? "less than or equal to" : "less than"} ${i.maximum}` : i.type === "date" ? n = `Date must be ${i.exact ? "exactly" : i.inclusive ? "smaller than or equal to" : "smaller than"} ${new Date(Number(i.maximum))}` : n = "Invalid input";
      break;
    case p.custom:
      n = "Invalid input";
      break;
    case p.invalid_intersection_types:
      n = "Intersection results could not be merged";
      break;
    case p.not_multiple_of:
      n = `Number must be a multiple of ${i.multipleOf}`;
      break;
    case p.not_finite:
      n = "Number must be finite";
      break;
    default:
      n = t.defaultError, R.assertNever(i);
  }
  return { message: n };
};
let Ce = se;
function $e() {
  return Ce;
}
const Ne = (i) => {
  const { data: t, path: n, errorMaps: d, issueData: u } = i, m = [...n, ...u.path || []], _ = {
    ...u,
    path: m
  };
  if (u.message !== void 0)
    return {
      ...u,
      path: m,
      message: u.message
    };
  let f = "";
  const x = d.filter((k) => !!k).slice().reverse();
  for (const k of x)
    f = k(_, { data: t, defaultError: f }).message;
  return {
    ...u,
    path: m,
    message: f
  };
};
function h(i, t) {
  const n = $e(), d = Ne({
    issueData: t,
    data: i.data,
    path: i.path,
    errorMaps: [
      i.common.contextualErrorMap,
      // contextual error map is first priority
      i.schemaErrorMap,
      // then schema-bound map if available
      n,
      // then global override map
      n === se ? void 0 : se
      // then global default map
    ].filter((u) => !!u)
  });
  i.common.issues.push(d);
}
class T {
  constructor() {
    this.value = "valid";
  }
  dirty() {
    this.value === "valid" && (this.value = "dirty");
  }
  abort() {
    this.value !== "aborted" && (this.value = "aborted");
  }
  static mergeArray(t, n) {
    const d = [];
    for (const u of n) {
      if (u.status === "aborted")
        return q;
      u.status === "dirty" && t.dirty(), d.push(u.value);
    }
    return { status: t.value, value: d };
  }
  static async mergeObjectAsync(t, n) {
    const d = [];
    for (const u of n) {
      const m = await u.key, _ = await u.value;
      d.push({
        key: m,
        value: _
      });
    }
    return T.mergeObjectSync(t, d);
  }
  static mergeObjectSync(t, n) {
    const d = {};
    for (const u of n) {
      const { key: m, value: _ } = u;
      if (m.status === "aborted" || _.status === "aborted")
        return q;
      m.status === "dirty" && t.dirty(), _.status === "dirty" && t.dirty(), m.value !== "__proto__" && (typeof _.value < "u" || u.alwaysSet) && (d[m.value] = _.value);
    }
    return { status: t.value, value: d };
  }
}
const q = Object.freeze({
  status: "aborted"
}), H = (i) => ({ status: "dirty", value: i }), C = (i) => ({ status: "valid", value: i }), fe = (i) => i.status === "aborted", pe = (i) => i.status === "dirty", D = (i) => i.status === "valid", G = (i) => typeof Promise < "u" && i instanceof Promise;
var y;
(function(i) {
  i.errToObj = (t) => typeof t == "string" ? { message: t } : t || {}, i.toString = (t) => typeof t == "string" ? t : t?.message;
})(y || (y = {}));
class N {
  constructor(t, n, d, u) {
    this._cachedPath = [], this.parent = t, this.data = n, this._path = d, this._key = u;
  }
  get path() {
    return this._cachedPath.length || (Array.isArray(this._key) ? this._cachedPath.push(...this._path, ...this._key) : this._cachedPath.push(...this._path, this._key)), this._cachedPath;
  }
}
const he = (i, t) => {
  if (D(t))
    return { success: !0, data: t.value };
  if (!i.common.issues.length)
    throw new Error("Validation failed but no issues detected.");
  return {
    success: !1,
    get error() {
      if (this._error)
        return this._error;
      const n = new z(i.common.issues);
      return this._error = n, this._error;
    }
  };
};
function O(i) {
  if (!i)
    return {};
  const { errorMap: t, invalid_type_error: n, required_error: d, description: u } = i;
  if (t && (n || d))
    throw new Error(`Can't use "invalid_type_error" or "required_error" in conjunction with custom error map.`);
  return t ? { errorMap: t, description: u } : { errorMap: (_, f) => {
    const { message: x } = i;
    return _.code === "invalid_enum_value" ? { message: x ?? f.defaultError } : typeof f.data > "u" ? { message: x ?? d ?? f.defaultError } : _.code !== "invalid_type" ? { message: f.defaultError } : { message: x ?? n ?? f.defaultError };
  }, description: u };
}
class A {
  get description() {
    return this._def.description;
  }
  _getType(t) {
    return I(t.data);
  }
  _getOrReturnCtx(t, n) {
    return n || {
      common: t.parent.common,
      data: t.data,
      parsedType: I(t.data),
      schemaErrorMap: this._def.errorMap,
      path: t.path,
      parent: t.parent
    };
  }
  _processInputParams(t) {
    return {
      status: new T(),
      ctx: {
        common: t.parent.common,
        data: t.data,
        parsedType: I(t.data),
        schemaErrorMap: this._def.errorMap,
        path: t.path,
        parent: t.parent
      }
    };
  }
  _parseSync(t) {
    const n = this._parse(t);
    if (G(n))
      throw new Error("Synchronous parse encountered promise.");
    return n;
  }
  _parseAsync(t) {
    const n = this._parse(t);
    return Promise.resolve(n);
  }
  parse(t, n) {
    const d = this.safeParse(t, n);
    if (d.success)
      return d.data;
    throw d.error;
  }
  safeParse(t, n) {
    const d = {
      common: {
        issues: [],
        async: n?.async ?? !1,
        contextualErrorMap: n?.errorMap
      },
      path: n?.path || [],
      schemaErrorMap: this._def.errorMap,
      parent: null,
      data: t,
      parsedType: I(t)
    }, u = this._parseSync({ data: t, path: d.path, parent: d });
    return he(d, u);
  }
  "~validate"(t) {
    const n = {
      common: {
        issues: [],
        async: !!this["~standard"].async
      },
      path: [],
      schemaErrorMap: this._def.errorMap,
      parent: null,
      data: t,
      parsedType: I(t)
    };
    if (!this["~standard"].async)
      try {
        const d = this._parseSync({ data: t, path: [], parent: n });
        return D(d) ? {
          value: d.value
        } : {
          issues: n.common.issues
        };
      } catch (d) {
        d?.message?.toLowerCase()?.includes("encountered") && (this["~standard"].async = !0), n.common = {
          issues: [],
          async: !0
        };
      }
    return this._parseAsync({ data: t, path: [], parent: n }).then((d) => D(d) ? {
      value: d.value
    } : {
      issues: n.common.issues
    });
  }
  async parseAsync(t, n) {
    const d = await this.safeParseAsync(t, n);
    if (d.success)
      return d.data;
    throw d.error;
  }
  async safeParseAsync(t, n) {
    const d = {
      common: {
        issues: [],
        contextualErrorMap: n?.errorMap,
        async: !0
      },
      path: n?.path || [],
      schemaErrorMap: this._def.errorMap,
      parent: null,
      data: t,
      parsedType: I(t)
    }, u = this._parse({ data: t, path: d.path, parent: d }), m = await (G(u) ? u : Promise.resolve(u));
    return he(d, m);
  }
  refine(t, n) {
    const d = (u) => typeof n == "string" || typeof n > "u" ? { message: n } : typeof n == "function" ? n(u) : n;
    return this._refinement((u, m) => {
      const _ = t(u), f = () => m.addIssue({
        code: p.custom,
        ...d(u)
      });
      return typeof Promise < "u" && _ instanceof Promise ? _.then((x) => x ? !0 : (f(), !1)) : _ ? !0 : (f(), !1);
    });
  }
  refinement(t, n) {
    return this._refinement((d, u) => t(d) ? !0 : (u.addIssue(typeof n == "function" ? n(d, u) : n), !1));
  }
  _refinement(t) {
    return new F({
      schema: this,
      typeName: w.ZodEffects,
      effect: { type: "refinement", refinement: t }
    });
  }
  superRefine(t) {
    return this._refinement(t);
  }
  constructor(t) {
    this.spa = this.safeParseAsync, this._def = t, this.parse = this.parse.bind(this), this.safeParse = this.safeParse.bind(this), this.parseAsync = this.parseAsync.bind(this), this.safeParseAsync = this.safeParseAsync.bind(this), this.spa = this.spa.bind(this), this.refine = this.refine.bind(this), this.refinement = this.refinement.bind(this), this.superRefine = this.superRefine.bind(this), this.optional = this.optional.bind(this), this.nullable = this.nullable.bind(this), this.nullish = this.nullish.bind(this), this.array = this.array.bind(this), this.promise = this.promise.bind(this), this.or = this.or.bind(this), this.and = this.and.bind(this), this.transform = this.transform.bind(this), this.brand = this.brand.bind(this), this.default = this.default.bind(this), this.catch = this.catch.bind(this), this.describe = this.describe.bind(this), this.pipe = this.pipe.bind(this), this.readonly = this.readonly.bind(this), this.isNullable = this.isNullable.bind(this), this.isOptional = this.isOptional.bind(this), this["~standard"] = {
      version: 1,
      vendor: "zod",
      validate: (n) => this["~validate"](n)
    };
  }
  optional() {
    return V.create(this, this._def);
  }
  nullable() {
    return W.create(this, this._def);
  }
  nullish() {
    return this.nullable().optional();
  }
  array() {
    return $.create(this);
  }
  promise() {
    return ie.create(this, this._def);
  }
  or(t) {
    return K.create([this, t], this._def);
  }
  and(t) {
    return ee.create(this, t, this._def);
  }
  transform(t) {
    return new F({
      ...O(this._def),
      schema: this,
      typeName: w.ZodEffects,
      effect: { type: "transform", transform: t }
    });
  }
  default(t) {
    const n = typeof t == "function" ? t : () => t;
    return new ce({
      ...O(this._def),
      innerType: this,
      defaultValue: n,
      typeName: w.ZodDefault
    });
  }
  brand() {
    return new nt({
      typeName: w.ZodBranded,
      type: this,
      ...O(this._def)
    });
  }
  catch(t) {
    const n = typeof t == "function" ? t : () => t;
    return new ue({
      ...O(this._def),
      innerType: this,
      catchValue: n,
      typeName: w.ZodCatch
    });
  }
  describe(t) {
    const n = this.constructor;
    return new n({
      ...this._def,
      description: t
    });
  }
  pipe(t) {
    return me.create(this, t);
  }
  readonly() {
    return le.create(this);
  }
  isOptional() {
    return this.safeParse(void 0).success;
  }
  isNullable() {
    return this.safeParse(null).success;
  }
}
const Ze = /^c[^\s-]{8,}$/i, ze = /^[0-9a-z]+$/, Ie = /^[0-9A-HJKMNP-TV-Z]{26}$/i, Ve = /^[0-9a-fA-F]{8}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{12}$/i, Le = /^[a-z0-9_-]{21}$/i, Me = /^[A-Za-z0-9-_]+\.[A-Za-z0-9-_]+\.[A-Za-z0-9-_]*$/, Pe = /^[-+]?P(?!$)(?:(?:[-+]?\d+Y)|(?:[-+]?\d+[.,]\d+Y$))?(?:(?:[-+]?\d+M)|(?:[-+]?\d+[.,]\d+M$))?(?:(?:[-+]?\d+W)|(?:[-+]?\d+[.,]\d+W$))?(?:(?:[-+]?\d+D)|(?:[-+]?\d+[.,]\d+D$))?(?:T(?=[\d+-])(?:(?:[-+]?\d+H)|(?:[-+]?\d+[.,]\d+H$))?(?:(?:[-+]?\d+M)|(?:[-+]?\d+[.,]\d+M$))?(?:[-+]?\d+(?:[.,]\d+)?S)?)??$/, De = /^(?!\.)(?!.*\.\.)([A-Z0-9_'+\-\.]*)[A-Z0-9_+-]@([A-Z0-9][A-Z0-9\-]*\.)+[A-Z]{2,}$/i, Be = "^(\\p{Extended_Pictographic}|\\p{Emoji_Component})+$";
let ne;
const Ue = /^(?:(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])$/, Fe = /^(?:(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])\/(3[0-2]|[12]?[0-9])$/, We = /^(([0-9a-fA-F]{1,4}:){7,7}[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,7}:|([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}|([0-9a-fA-F]{1,4}:){1,4}(:[0-9a-fA-F]{1,4}){1,3}|([0-9a-fA-F]{1,4}:){1,3}(:[0-9a-fA-F]{1,4}){1,4}|([0-9a-fA-F]{1,4}:){1,2}(:[0-9a-fA-F]{1,4}){1,5}|[0-9a-fA-F]{1,4}:((:[0-9a-fA-F]{1,4}){1,6})|:((:[0-9a-fA-F]{1,4}){1,7}|:)|fe80:(:[0-9a-fA-F]{0,4}){0,4}%[0-9a-zA-Z]{1,}|::(ffff(:0{1,4}){0,1}:){0,1}((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])|([0-9a-fA-F]{1,4}:){1,4}:((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9]))$/, Je = /^(([0-9a-fA-F]{1,4}:){7,7}[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,7}:|([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}|([0-9a-fA-F]{1,4}:){1,4}(:[0-9a-fA-F]{1,4}){1,3}|([0-9a-fA-F]{1,4}:){1,3}(:[0-9a-fA-F]{1,4}){1,4}|([0-9a-fA-F]{1,4}:){1,2}(:[0-9a-fA-F]{1,4}){1,5}|[0-9a-fA-F]{1,4}:((:[0-9a-fA-F]{1,4}){1,6})|:((:[0-9a-fA-F]{1,4}){1,7}|:)|fe80:(:[0-9a-fA-F]{0,4}){0,4}%[0-9a-zA-Z]{1,}|::(ffff(:0{1,4}){0,1}:){0,1}((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])|([0-9a-fA-F]{1,4}:){1,4}:((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9]))\/(12[0-8]|1[01][0-9]|[1-9]?[0-9])$/, He = /^([0-9a-zA-Z+/]{4})*(([0-9a-zA-Z+/]{2}==)|([0-9a-zA-Z+/]{3}=))?$/, Ye = /^([0-9a-zA-Z-_]{4})*(([0-9a-zA-Z-_]{2}(==)?)|([0-9a-zA-Z-_]{3}(=)?))?$/, Ae = "((\\d\\d[2468][048]|\\d\\d[13579][26]|\\d\\d0[48]|[02468][048]00|[13579][26]00)-02-29|\\d{4}-((0[13578]|1[02])-(0[1-9]|[12]\\d|3[01])|(0[469]|11)-(0[1-9]|[12]\\d|30)|(02)-(0[1-9]|1\\d|2[0-8])))", Qe = new RegExp(`^${Ae}$`);
function Re(i) {
  let t = "[0-5]\\d";
  i.precision ? t = `${t}\\.\\d{${i.precision}}` : i.precision == null && (t = `${t}(\\.\\d+)?`);
  const n = i.precision ? "+" : "?";
  return `([01]\\d|2[0-3]):[0-5]\\d(:${t})${n}`;
}
function Ge(i) {
  return new RegExp(`^${Re(i)}$`);
}
function Xe(i) {
  let t = `${Ae}T${Re(i)}`;
  const n = [];
  return n.push(i.local ? "Z?" : "Z"), i.offset && n.push("([+-]\\d{2}:?\\d{2})"), t = `${t}(${n.join("|")})`, new RegExp(`^${t}$`);
}
function Ke(i, t) {
  return !!((t === "v4" || !t) && Ue.test(i) || (t === "v6" || !t) && We.test(i));
}
function et(i, t) {
  if (!Me.test(i))
    return !1;
  try {
    const [n] = i.split(".");
    if (!n)
      return !1;
    const d = n.replace(/-/g, "+").replace(/_/g, "/").padEnd(n.length + (4 - n.length % 4) % 4, "="), u = JSON.parse(atob(d));
    return !(typeof u != "object" || u === null || "typ" in u && u?.typ !== "JWT" || !u.alg || t && u.alg !== t);
  } catch {
    return !1;
  }
}
function tt(i, t) {
  return !!((t === "v4" || !t) && Fe.test(i) || (t === "v6" || !t) && Je.test(i));
}
class Z extends A {
  _parse(t) {
    if (this._def.coerce && (t.data = String(t.data)), this._getType(t) !== v.string) {
      const m = this._getOrReturnCtx(t);
      return h(m, {
        code: p.invalid_type,
        expected: v.string,
        received: m.parsedType
      }), q;
    }
    const d = new T();
    let u;
    for (const m of this._def.checks)
      if (m.kind === "min")
        t.data.length < m.value && (u = this._getOrReturnCtx(t, u), h(u, {
          code: p.too_small,
          minimum: m.value,
          type: "string",
          inclusive: !0,
          exact: !1,
          message: m.message
        }), d.dirty());
      else if (m.kind === "max")
        t.data.length > m.value && (u = this._getOrReturnCtx(t, u), h(u, {
          code: p.too_big,
          maximum: m.value,
          type: "string",
          inclusive: !0,
          exact: !1,
          message: m.message
        }), d.dirty());
      else if (m.kind === "length") {
        const _ = t.data.length > m.value, f = t.data.length < m.value;
        (_ || f) && (u = this._getOrReturnCtx(t, u), _ ? h(u, {
          code: p.too_big,
          maximum: m.value,
          type: "string",
          inclusive: !0,
          exact: !0,
          message: m.message
        }) : f && h(u, {
          code: p.too_small,
          minimum: m.value,
          type: "string",
          inclusive: !0,
          exact: !0,
          message: m.message
        }), d.dirty());
      } else if (m.kind === "email")
        De.test(t.data) || (u = this._getOrReturnCtx(t, u), h(u, {
          validation: "email",
          code: p.invalid_string,
          message: m.message
        }), d.dirty());
      else if (m.kind === "emoji")
        ne || (ne = new RegExp(Be, "u")), ne.test(t.data) || (u = this._getOrReturnCtx(t, u), h(u, {
          validation: "emoji",
          code: p.invalid_string,
          message: m.message
        }), d.dirty());
      else if (m.kind === "uuid")
        Ve.test(t.data) || (u = this._getOrReturnCtx(t, u), h(u, {
          validation: "uuid",
          code: p.invalid_string,
          message: m.message
        }), d.dirty());
      else if (m.kind === "nanoid")
        Le.test(t.data) || (u = this._getOrReturnCtx(t, u), h(u, {
          validation: "nanoid",
          code: p.invalid_string,
          message: m.message
        }), d.dirty());
      else if (m.kind === "cuid")
        Ze.test(t.data) || (u = this._getOrReturnCtx(t, u), h(u, {
          validation: "cuid",
          code: p.invalid_string,
          message: m.message
        }), d.dirty());
      else if (m.kind === "cuid2")
        ze.test(t.data) || (u = this._getOrReturnCtx(t, u), h(u, {
          validation: "cuid2",
          code: p.invalid_string,
          message: m.message
        }), d.dirty());
      else if (m.kind === "ulid")
        Ie.test(t.data) || (u = this._getOrReturnCtx(t, u), h(u, {
          validation: "ulid",
          code: p.invalid_string,
          message: m.message
        }), d.dirty());
      else if (m.kind === "url")
        try {
          new URL(t.data);
        } catch {
          u = this._getOrReturnCtx(t, u), h(u, {
            validation: "url",
            code: p.invalid_string,
            message: m.message
          }), d.dirty();
        }
      else m.kind === "regex" ? (m.regex.lastIndex = 0, m.regex.test(t.data) || (u = this._getOrReturnCtx(t, u), h(u, {
        validation: "regex",
        code: p.invalid_string,
        message: m.message
      }), d.dirty())) : m.kind === "trim" ? t.data = t.data.trim() : m.kind === "includes" ? t.data.includes(m.value, m.position) || (u = this._getOrReturnCtx(t, u), h(u, {
        code: p.invalid_string,
        validation: { includes: m.value, position: m.position },
        message: m.message
      }), d.dirty()) : m.kind === "toLowerCase" ? t.data = t.data.toLowerCase() : m.kind === "toUpperCase" ? t.data = t.data.toUpperCase() : m.kind === "startsWith" ? t.data.startsWith(m.value) || (u = this._getOrReturnCtx(t, u), h(u, {
        code: p.invalid_string,
        validation: { startsWith: m.value },
        message: m.message
      }), d.dirty()) : m.kind === "endsWith" ? t.data.endsWith(m.value) || (u = this._getOrReturnCtx(t, u), h(u, {
        code: p.invalid_string,
        validation: { endsWith: m.value },
        message: m.message
      }), d.dirty()) : m.kind === "datetime" ? Xe(m).test(t.data) || (u = this._getOrReturnCtx(t, u), h(u, {
        code: p.invalid_string,
        validation: "datetime",
        message: m.message
      }), d.dirty()) : m.kind === "date" ? Qe.test(t.data) || (u = this._getOrReturnCtx(t, u), h(u, {
        code: p.invalid_string,
        validation: "date",
        message: m.message
      }), d.dirty()) : m.kind === "time" ? Ge(m).test(t.data) || (u = this._getOrReturnCtx(t, u), h(u, {
        code: p.invalid_string,
        validation: "time",
        message: m.message
      }), d.dirty()) : m.kind === "duration" ? Pe.test(t.data) || (u = this._getOrReturnCtx(t, u), h(u, {
        validation: "duration",
        code: p.invalid_string,
        message: m.message
      }), d.dirty()) : m.kind === "ip" ? Ke(t.data, m.version) || (u = this._getOrReturnCtx(t, u), h(u, {
        validation: "ip",
        code: p.invalid_string,
        message: m.message
      }), d.dirty()) : m.kind === "jwt" ? et(t.data, m.alg) || (u = this._getOrReturnCtx(t, u), h(u, {
        validation: "jwt",
        code: p.invalid_string,
        message: m.message
      }), d.dirty()) : m.kind === "cidr" ? tt(t.data, m.version) || (u = this._getOrReturnCtx(t, u), h(u, {
        validation: "cidr",
        code: p.invalid_string,
        message: m.message
      }), d.dirty()) : m.kind === "base64" ? He.test(t.data) || (u = this._getOrReturnCtx(t, u), h(u, {
        validation: "base64",
        code: p.invalid_string,
        message: m.message
      }), d.dirty()) : m.kind === "base64url" ? Ye.test(t.data) || (u = this._getOrReturnCtx(t, u), h(u, {
        validation: "base64url",
        code: p.invalid_string,
        message: m.message
      }), d.dirty()) : R.assertNever(m);
    return { status: d.value, value: t.data };
  }
  _regex(t, n, d) {
    return this.refinement((u) => t.test(u), {
      validation: n,
      code: p.invalid_string,
      ...y.errToObj(d)
    });
  }
  _addCheck(t) {
    return new Z({
      ...this._def,
      checks: [...this._def.checks, t]
    });
  }
  email(t) {
    return this._addCheck({ kind: "email", ...y.errToObj(t) });
  }
  url(t) {
    return this._addCheck({ kind: "url", ...y.errToObj(t) });
  }
  emoji(t) {
    return this._addCheck({ kind: "emoji", ...y.errToObj(t) });
  }
  uuid(t) {
    return this._addCheck({ kind: "uuid", ...y.errToObj(t) });
  }
  nanoid(t) {
    return this._addCheck({ kind: "nanoid", ...y.errToObj(t) });
  }
  cuid(t) {
    return this._addCheck({ kind: "cuid", ...y.errToObj(t) });
  }
  cuid2(t) {
    return this._addCheck({ kind: "cuid2", ...y.errToObj(t) });
  }
  ulid(t) {
    return this._addCheck({ kind: "ulid", ...y.errToObj(t) });
  }
  base64(t) {
    return this._addCheck({ kind: "base64", ...y.errToObj(t) });
  }
  base64url(t) {
    return this._addCheck({
      kind: "base64url",
      ...y.errToObj(t)
    });
  }
  jwt(t) {
    return this._addCheck({ kind: "jwt", ...y.errToObj(t) });
  }
  ip(t) {
    return this._addCheck({ kind: "ip", ...y.errToObj(t) });
  }
  cidr(t) {
    return this._addCheck({ kind: "cidr", ...y.errToObj(t) });
  }
  datetime(t) {
    return typeof t == "string" ? this._addCheck({
      kind: "datetime",
      precision: null,
      offset: !1,
      local: !1,
      message: t
    }) : this._addCheck({
      kind: "datetime",
      precision: typeof t?.precision > "u" ? null : t?.precision,
      offset: t?.offset ?? !1,
      local: t?.local ?? !1,
      ...y.errToObj(t?.message)
    });
  }
  date(t) {
    return this._addCheck({ kind: "date", message: t });
  }
  time(t) {
    return typeof t == "string" ? this._addCheck({
      kind: "time",
      precision: null,
      message: t
    }) : this._addCheck({
      kind: "time",
      precision: typeof t?.precision > "u" ? null : t?.precision,
      ...y.errToObj(t?.message)
    });
  }
  duration(t) {
    return this._addCheck({ kind: "duration", ...y.errToObj(t) });
  }
  regex(t, n) {
    return this._addCheck({
      kind: "regex",
      regex: t,
      ...y.errToObj(n)
    });
  }
  includes(t, n) {
    return this._addCheck({
      kind: "includes",
      value: t,
      position: n?.position,
      ...y.errToObj(n?.message)
    });
  }
  startsWith(t, n) {
    return this._addCheck({
      kind: "startsWith",
      value: t,
      ...y.errToObj(n)
    });
  }
  endsWith(t, n) {
    return this._addCheck({
      kind: "endsWith",
      value: t,
      ...y.errToObj(n)
    });
  }
  min(t, n) {
    return this._addCheck({
      kind: "min",
      value: t,
      ...y.errToObj(n)
    });
  }
  max(t, n) {
    return this._addCheck({
      kind: "max",
      value: t,
      ...y.errToObj(n)
    });
  }
  length(t, n) {
    return this._addCheck({
      kind: "length",
      value: t,
      ...y.errToObj(n)
    });
  }
  /**
   * Equivalent to `.min(1)`
   */
  nonempty(t) {
    return this.min(1, y.errToObj(t));
  }
  trim() {
    return new Z({
      ...this._def,
      checks: [...this._def.checks, { kind: "trim" }]
    });
  }
  toLowerCase() {
    return new Z({
      ...this._def,
      checks: [...this._def.checks, { kind: "toLowerCase" }]
    });
  }
  toUpperCase() {
    return new Z({
      ...this._def,
      checks: [...this._def.checks, { kind: "toUpperCase" }]
    });
  }
  get isDatetime() {
    return !!this._def.checks.find((t) => t.kind === "datetime");
  }
  get isDate() {
    return !!this._def.checks.find((t) => t.kind === "date");
  }
  get isTime() {
    return !!this._def.checks.find((t) => t.kind === "time");
  }
  get isDuration() {
    return !!this._def.checks.find((t) => t.kind === "duration");
  }
  get isEmail() {
    return !!this._def.checks.find((t) => t.kind === "email");
  }
  get isURL() {
    return !!this._def.checks.find((t) => t.kind === "url");
  }
  get isEmoji() {
    return !!this._def.checks.find((t) => t.kind === "emoji");
  }
  get isUUID() {
    return !!this._def.checks.find((t) => t.kind === "uuid");
  }
  get isNANOID() {
    return !!this._def.checks.find((t) => t.kind === "nanoid");
  }
  get isCUID() {
    return !!this._def.checks.find((t) => t.kind === "cuid");
  }
  get isCUID2() {
    return !!this._def.checks.find((t) => t.kind === "cuid2");
  }
  get isULID() {
    return !!this._def.checks.find((t) => t.kind === "ulid");
  }
  get isIP() {
    return !!this._def.checks.find((t) => t.kind === "ip");
  }
  get isCIDR() {
    return !!this._def.checks.find((t) => t.kind === "cidr");
  }
  get isBase64() {
    return !!this._def.checks.find((t) => t.kind === "base64");
  }
  get isBase64url() {
    return !!this._def.checks.find((t) => t.kind === "base64url");
  }
  get minLength() {
    let t = null;
    for (const n of this._def.checks)
      n.kind === "min" && (t === null || n.value > t) && (t = n.value);
    return t;
  }
  get maxLength() {
    let t = null;
    for (const n of this._def.checks)
      n.kind === "max" && (t === null || n.value < t) && (t = n.value);
    return t;
  }
}
Z.create = (i) => new Z({
  checks: [],
  typeName: w.ZodString,
  coerce: i?.coerce ?? !1,
  ...O(i)
});
function it(i, t) {
  const n = (i.toString().split(".")[1] || "").length, d = (t.toString().split(".")[1] || "").length, u = n > d ? n : d, m = Number.parseInt(i.toFixed(u).replace(".", "")), _ = Number.parseInt(t.toFixed(u).replace(".", ""));
  return m % _ / 10 ** u;
}
class B extends A {
  constructor() {
    super(...arguments), this.min = this.gte, this.max = this.lte, this.step = this.multipleOf;
  }
  _parse(t) {
    if (this._def.coerce && (t.data = Number(t.data)), this._getType(t) !== v.number) {
      const m = this._getOrReturnCtx(t);
      return h(m, {
        code: p.invalid_type,
        expected: v.number,
        received: m.parsedType
      }), q;
    }
    let d;
    const u = new T();
    for (const m of this._def.checks)
      m.kind === "int" ? R.isInteger(t.data) || (d = this._getOrReturnCtx(t, d), h(d, {
        code: p.invalid_type,
        expected: "integer",
        received: "float",
        message: m.message
      }), u.dirty()) : m.kind === "min" ? (m.inclusive ? t.data < m.value : t.data <= m.value) && (d = this._getOrReturnCtx(t, d), h(d, {
        code: p.too_small,
        minimum: m.value,
        type: "number",
        inclusive: m.inclusive,
        exact: !1,
        message: m.message
      }), u.dirty()) : m.kind === "max" ? (m.inclusive ? t.data > m.value : t.data >= m.value) && (d = this._getOrReturnCtx(t, d), h(d, {
        code: p.too_big,
        maximum: m.value,
        type: "number",
        inclusive: m.inclusive,
        exact: !1,
        message: m.message
      }), u.dirty()) : m.kind === "multipleOf" ? it(t.data, m.value) !== 0 && (d = this._getOrReturnCtx(t, d), h(d, {
        code: p.not_multiple_of,
        multipleOf: m.value,
        message: m.message
      }), u.dirty()) : m.kind === "finite" ? Number.isFinite(t.data) || (d = this._getOrReturnCtx(t, d), h(d, {
        code: p.not_finite,
        message: m.message
      }), u.dirty()) : R.assertNever(m);
    return { status: u.value, value: t.data };
  }
  gte(t, n) {
    return this.setLimit("min", t, !0, y.toString(n));
  }
  gt(t, n) {
    return this.setLimit("min", t, !1, y.toString(n));
  }
  lte(t, n) {
    return this.setLimit("max", t, !0, y.toString(n));
  }
  lt(t, n) {
    return this.setLimit("max", t, !1, y.toString(n));
  }
  setLimit(t, n, d, u) {
    return new B({
      ...this._def,
      checks: [
        ...this._def.checks,
        {
          kind: t,
          value: n,
          inclusive: d,
          message: y.toString(u)
        }
      ]
    });
  }
  _addCheck(t) {
    return new B({
      ...this._def,
      checks: [...this._def.checks, t]
    });
  }
  int(t) {
    return this._addCheck({
      kind: "int",
      message: y.toString(t)
    });
  }
  positive(t) {
    return this._addCheck({
      kind: "min",
      value: 0,
      inclusive: !1,
      message: y.toString(t)
    });
  }
  negative(t) {
    return this._addCheck({
      kind: "max",
      value: 0,
      inclusive: !1,
      message: y.toString(t)
    });
  }
  nonpositive(t) {
    return this._addCheck({
      kind: "max",
      value: 0,
      inclusive: !0,
      message: y.toString(t)
    });
  }
  nonnegative(t) {
    return this._addCheck({
      kind: "min",
      value: 0,
      inclusive: !0,
      message: y.toString(t)
    });
  }
  multipleOf(t, n) {
    return this._addCheck({
      kind: "multipleOf",
      value: t,
      message: y.toString(n)
    });
  }
  finite(t) {
    return this._addCheck({
      kind: "finite",
      message: y.toString(t)
    });
  }
  safe(t) {
    return this._addCheck({
      kind: "min",
      inclusive: !0,
      value: Number.MIN_SAFE_INTEGER,
      message: y.toString(t)
    })._addCheck({
      kind: "max",
      inclusive: !0,
      value: Number.MAX_SAFE_INTEGER,
      message: y.toString(t)
    });
  }
  get minValue() {
    let t = null;
    for (const n of this._def.checks)
      n.kind === "min" && (t === null || n.value > t) && (t = n.value);
    return t;
  }
  get maxValue() {
    let t = null;
    for (const n of this._def.checks)
      n.kind === "max" && (t === null || n.value < t) && (t = n.value);
    return t;
  }
  get isInt() {
    return !!this._def.checks.find((t) => t.kind === "int" || t.kind === "multipleOf" && R.isInteger(t.value));
  }
  get isFinite() {
    let t = null, n = null;
    for (const d of this._def.checks) {
      if (d.kind === "finite" || d.kind === "int" || d.kind === "multipleOf")
        return !0;
      d.kind === "min" ? (n === null || d.value > n) && (n = d.value) : d.kind === "max" && (t === null || d.value < t) && (t = d.value);
    }
    return Number.isFinite(n) && Number.isFinite(t);
  }
}
B.create = (i) => new B({
  checks: [],
  typeName: w.ZodNumber,
  coerce: i?.coerce || !1,
  ...O(i)
});
class Y extends A {
  constructor() {
    super(...arguments), this.min = this.gte, this.max = this.lte;
  }
  _parse(t) {
    if (this._def.coerce)
      try {
        t.data = BigInt(t.data);
      } catch {
        return this._getInvalidInput(t);
      }
    if (this._getType(t) !== v.bigint)
      return this._getInvalidInput(t);
    let d;
    const u = new T();
    for (const m of this._def.checks)
      m.kind === "min" ? (m.inclusive ? t.data < m.value : t.data <= m.value) && (d = this._getOrReturnCtx(t, d), h(d, {
        code: p.too_small,
        type: "bigint",
        minimum: m.value,
        inclusive: m.inclusive,
        message: m.message
      }), u.dirty()) : m.kind === "max" ? (m.inclusive ? t.data > m.value : t.data >= m.value) && (d = this._getOrReturnCtx(t, d), h(d, {
        code: p.too_big,
        type: "bigint",
        maximum: m.value,
        inclusive: m.inclusive,
        message: m.message
      }), u.dirty()) : m.kind === "multipleOf" ? t.data % m.value !== BigInt(0) && (d = this._getOrReturnCtx(t, d), h(d, {
        code: p.not_multiple_of,
        multipleOf: m.value,
        message: m.message
      }), u.dirty()) : R.assertNever(m);
    return { status: u.value, value: t.data };
  }
  _getInvalidInput(t) {
    const n = this._getOrReturnCtx(t);
    return h(n, {
      code: p.invalid_type,
      expected: v.bigint,
      received: n.parsedType
    }), q;
  }
  gte(t, n) {
    return this.setLimit("min", t, !0, y.toString(n));
  }
  gt(t, n) {
    return this.setLimit("min", t, !1, y.toString(n));
  }
  lte(t, n) {
    return this.setLimit("max", t, !0, y.toString(n));
  }
  lt(t, n) {
    return this.setLimit("max", t, !1, y.toString(n));
  }
  setLimit(t, n, d, u) {
    return new Y({
      ...this._def,
      checks: [
        ...this._def.checks,
        {
          kind: t,
          value: n,
          inclusive: d,
          message: y.toString(u)
        }
      ]
    });
  }
  _addCheck(t) {
    return new Y({
      ...this._def,
      checks: [...this._def.checks, t]
    });
  }
  positive(t) {
    return this._addCheck({
      kind: "min",
      value: BigInt(0),
      inclusive: !1,
      message: y.toString(t)
    });
  }
  negative(t) {
    return this._addCheck({
      kind: "max",
      value: BigInt(0),
      inclusive: !1,
      message: y.toString(t)
    });
  }
  nonpositive(t) {
    return this._addCheck({
      kind: "max",
      value: BigInt(0),
      inclusive: !0,
      message: y.toString(t)
    });
  }
  nonnegative(t) {
    return this._addCheck({
      kind: "min",
      value: BigInt(0),
      inclusive: !0,
      message: y.toString(t)
    });
  }
  multipleOf(t, n) {
    return this._addCheck({
      kind: "multipleOf",
      value: t,
      message: y.toString(n)
    });
  }
  get minValue() {
    let t = null;
    for (const n of this._def.checks)
      n.kind === "min" && (t === null || n.value > t) && (t = n.value);
    return t;
  }
  get maxValue() {
    let t = null;
    for (const n of this._def.checks)
      n.kind === "max" && (t === null || n.value < t) && (t = n.value);
    return t;
  }
}
Y.create = (i) => new Y({
  checks: [],
  typeName: w.ZodBigInt,
  coerce: i?.coerce ?? !1,
  ...O(i)
});
class ae extends A {
  _parse(t) {
    if (this._def.coerce && (t.data = !!t.data), this._getType(t) !== v.boolean) {
      const d = this._getOrReturnCtx(t);
      return h(d, {
        code: p.invalid_type,
        expected: v.boolean,
        received: d.parsedType
      }), q;
    }
    return C(t.data);
  }
}
ae.create = (i) => new ae({
  typeName: w.ZodBoolean,
  coerce: i?.coerce || !1,
  ...O(i)
});
class X extends A {
  _parse(t) {
    if (this._def.coerce && (t.data = new Date(t.data)), this._getType(t) !== v.date) {
      const m = this._getOrReturnCtx(t);
      return h(m, {
        code: p.invalid_type,
        expected: v.date,
        received: m.parsedType
      }), q;
    }
    if (Number.isNaN(t.data.getTime())) {
      const m = this._getOrReturnCtx(t);
      return h(m, {
        code: p.invalid_date
      }), q;
    }
    const d = new T();
    let u;
    for (const m of this._def.checks)
      m.kind === "min" ? t.data.getTime() < m.value && (u = this._getOrReturnCtx(t, u), h(u, {
        code: p.too_small,
        message: m.message,
        inclusive: !0,
        exact: !1,
        minimum: m.value,
        type: "date"
      }), d.dirty()) : m.kind === "max" ? t.data.getTime() > m.value && (u = this._getOrReturnCtx(t, u), h(u, {
        code: p.too_big,
        message: m.message,
        inclusive: !0,
        exact: !1,
        maximum: m.value,
        type: "date"
      }), d.dirty()) : R.assertNever(m);
    return {
      status: d.value,
      value: new Date(t.data.getTime())
    };
  }
  _addCheck(t) {
    return new X({
      ...this._def,
      checks: [...this._def.checks, t]
    });
  }
  min(t, n) {
    return this._addCheck({
      kind: "min",
      value: t.getTime(),
      message: y.toString(n)
    });
  }
  max(t, n) {
    return this._addCheck({
      kind: "max",
      value: t.getTime(),
      message: y.toString(n)
    });
  }
  get minDate() {
    let t = null;
    for (const n of this._def.checks)
      n.kind === "min" && (t === null || n.value > t) && (t = n.value);
    return t != null ? new Date(t) : null;
  }
  get maxDate() {
    let t = null;
    for (const n of this._def.checks)
      n.kind === "max" && (t === null || n.value < t) && (t = n.value);
    return t != null ? new Date(t) : null;
  }
}
X.create = (i) => new X({
  checks: [],
  coerce: i?.coerce || !1,
  typeName: w.ZodDate,
  ...O(i)
});
class ge extends A {
  _parse(t) {
    if (this._getType(t) !== v.symbol) {
      const d = this._getOrReturnCtx(t);
      return h(d, {
        code: p.invalid_type,
        expected: v.symbol,
        received: d.parsedType
      }), q;
    }
    return C(t.data);
  }
}
ge.create = (i) => new ge({
  typeName: w.ZodSymbol,
  ...O(i)
});
class ve extends A {
  _parse(t) {
    if (this._getType(t) !== v.undefined) {
      const d = this._getOrReturnCtx(t);
      return h(d, {
        code: p.invalid_type,
        expected: v.undefined,
        received: d.parsedType
      }), q;
    }
    return C(t.data);
  }
}
ve.create = (i) => new ve({
  typeName: w.ZodUndefined,
  ...O(i)
});
class re extends A {
  _parse(t) {
    if (this._getType(t) !== v.null) {
      const d = this._getOrReturnCtx(t);
      return h(d, {
        code: p.invalid_type,
        expected: v.null,
        received: d.parsedType
      }), q;
    }
    return C(t.data);
  }
}
re.create = (i) => new re({
  typeName: w.ZodNull,
  ...O(i)
});
class ye extends A {
  constructor() {
    super(...arguments), this._any = !0;
  }
  _parse(t) {
    return C(t.data);
  }
}
ye.create = (i) => new ye({
  typeName: w.ZodAny,
  ...O(i)
});
class be extends A {
  constructor() {
    super(...arguments), this._unknown = !0;
  }
  _parse(t) {
    return C(t.data);
  }
}
be.create = (i) => new be({
  typeName: w.ZodUnknown,
  ...O(i)
});
class L extends A {
  _parse(t) {
    const n = this._getOrReturnCtx(t);
    return h(n, {
      code: p.invalid_type,
      expected: v.never,
      received: n.parsedType
    }), q;
  }
}
L.create = (i) => new L({
  typeName: w.ZodNever,
  ...O(i)
});
class xe extends A {
  _parse(t) {
    if (this._getType(t) !== v.undefined) {
      const d = this._getOrReturnCtx(t);
      return h(d, {
        code: p.invalid_type,
        expected: v.void,
        received: d.parsedType
      }), q;
    }
    return C(t.data);
  }
}
xe.create = (i) => new xe({
  typeName: w.ZodVoid,
  ...O(i)
});
class $ extends A {
  _parse(t) {
    const { ctx: n, status: d } = this._processInputParams(t), u = this._def;
    if (n.parsedType !== v.array)
      return h(n, {
        code: p.invalid_type,
        expected: v.array,
        received: n.parsedType
      }), q;
    if (u.exactLength !== null) {
      const _ = n.data.length > u.exactLength.value, f = n.data.length < u.exactLength.value;
      (_ || f) && (h(n, {
        code: _ ? p.too_big : p.too_small,
        minimum: f ? u.exactLength.value : void 0,
        maximum: _ ? u.exactLength.value : void 0,
        type: "array",
        inclusive: !0,
        exact: !0,
        message: u.exactLength.message
      }), d.dirty());
    }
    if (u.minLength !== null && n.data.length < u.minLength.value && (h(n, {
      code: p.too_small,
      minimum: u.minLength.value,
      type: "array",
      inclusive: !0,
      exact: !1,
      message: u.minLength.message
    }), d.dirty()), u.maxLength !== null && n.data.length > u.maxLength.value && (h(n, {
      code: p.too_big,
      maximum: u.maxLength.value,
      type: "array",
      inclusive: !0,
      exact: !1,
      message: u.maxLength.message
    }), d.dirty()), n.common.async)
      return Promise.all([...n.data].map((_, f) => u.type._parseAsync(new N(n, _, n.path, f)))).then((_) => T.mergeArray(d, _));
    const m = [...n.data].map((_, f) => u.type._parseSync(new N(n, _, n.path, f)));
    return T.mergeArray(d, m);
  }
  get element() {
    return this._def.type;
  }
  min(t, n) {
    return new $({
      ...this._def,
      minLength: { value: t, message: y.toString(n) }
    });
  }
  max(t, n) {
    return new $({
      ...this._def,
      maxLength: { value: t, message: y.toString(n) }
    });
  }
  length(t, n) {
    return new $({
      ...this._def,
      exactLength: { value: t, message: y.toString(n) }
    });
  }
  nonempty(t) {
    return this.min(1, t);
  }
}
$.create = (i, t) => new $({
  type: i,
  minLength: null,
  maxLength: null,
  exactLength: null,
  typeName: w.ZodArray,
  ...O(t)
});
function P(i) {
  if (i instanceof j) {
    const t = {};
    for (const n in i.shape) {
      const d = i.shape[n];
      t[n] = V.create(P(d));
    }
    return new j({
      ...i._def,
      shape: () => t
    });
  } else return i instanceof $ ? new $({
    ...i._def,
    type: P(i.element)
  }) : i instanceof V ? V.create(P(i.unwrap())) : i instanceof W ? W.create(P(i.unwrap())) : i instanceof M ? M.create(i.items.map((t) => P(t))) : i;
}
class j extends A {
  constructor() {
    super(...arguments), this._cached = null, this.nonstrict = this.passthrough, this.augment = this.extend;
  }
  _getCached() {
    if (this._cached !== null)
      return this._cached;
    const t = this._def.shape(), n = R.objectKeys(t);
    return this._cached = { shape: t, keys: n }, this._cached;
  }
  _parse(t) {
    if (this._getType(t) !== v.object) {
      const k = this._getOrReturnCtx(t);
      return h(k, {
        code: p.invalid_type,
        expected: v.object,
        received: k.parsedType
      }), q;
    }
    const { status: d, ctx: u } = this._processInputParams(t), { shape: m, keys: _ } = this._getCached(), f = [];
    if (!(this._def.catchall instanceof L && this._def.unknownKeys === "strip"))
      for (const k in u.data)
        _.includes(k) || f.push(k);
    const x = [];
    for (const k of _) {
      const S = m[k], J = u.data[k];
      x.push({
        key: { status: "valid", value: k },
        value: S._parse(new N(u, J, u.path, k)),
        alwaysSet: k in u.data
      });
    }
    if (this._def.catchall instanceof L) {
      const k = this._def.unknownKeys;
      if (k === "passthrough")
        for (const S of f)
          x.push({
            key: { status: "valid", value: S },
            value: { status: "valid", value: u.data[S] }
          });
      else if (k === "strict")
        f.length > 0 && (h(u, {
          code: p.unrecognized_keys,
          keys: f
        }), d.dirty());
      else if (k !== "strip") throw new Error("Internal ZodObject error: invalid unknownKeys value.");
    } else {
      const k = this._def.catchall;
      for (const S of f) {
        const J = u.data[S];
        x.push({
          key: { status: "valid", value: S },
          value: k._parse(
            new N(u, J, u.path, S)
            //, ctx.child(key), value, getParsedType(value)
          ),
          alwaysSet: S in u.data
        });
      }
    }
    return u.common.async ? Promise.resolve().then(async () => {
      const k = [];
      for (const S of x) {
        const J = await S.key, Ee = await S.value;
        k.push({
          key: J,
          value: Ee,
          alwaysSet: S.alwaysSet
        });
      }
      return k;
    }).then((k) => T.mergeObjectSync(d, k)) : T.mergeObjectSync(d, x);
  }
  get shape() {
    return this._def.shape();
  }
  strict(t) {
    return y.errToObj, new j({
      ...this._def,
      unknownKeys: "strict",
      ...t !== void 0 ? {
        errorMap: (n, d) => {
          const u = this._def.errorMap?.(n, d).message ?? d.defaultError;
          return n.code === "unrecognized_keys" ? {
            message: y.errToObj(t).message ?? u
          } : {
            message: u
          };
        }
      } : {}
    });
  }
  strip() {
    return new j({
      ...this._def,
      unknownKeys: "strip"
    });
  }
  passthrough() {
    return new j({
      ...this._def,
      unknownKeys: "passthrough"
    });
  }
  // const AugmentFactory =
  //   <Def extends ZodObjectDef>(def: Def) =>
  //   <Augmentation extends ZodRawShape>(
  //     augmentation: Augmentation
  //   ): ZodObject<
  //     extendShape<ReturnType<Def["shape"]>, Augmentation>,
  //     Def["unknownKeys"],
  //     Def["catchall"]
  //   > => {
  //     return new ZodObject({
  //       ...def,
  //       shape: () => ({
  //         ...def.shape(),
  //         ...augmentation,
  //       }),
  //     }) as any;
  //   };
  extend(t) {
    return new j({
      ...this._def,
      shape: () => ({
        ...this._def.shape(),
        ...t
      })
    });
  }
  /**
   * Prior to zod@1.0.12 there was a bug in the
   * inferred type of merged objects. Please
   * upgrade if you are experiencing issues.
   */
  merge(t) {
    return new j({
      unknownKeys: t._def.unknownKeys,
      catchall: t._def.catchall,
      shape: () => ({
        ...this._def.shape(),
        ...t._def.shape()
      }),
      typeName: w.ZodObject
    });
  }
  // merge<
  //   Incoming extends AnyZodObject,
  //   Augmentation extends Incoming["shape"],
  //   NewOutput extends {
  //     [k in keyof Augmentation | keyof Output]: k extends keyof Augmentation
  //       ? Augmentation[k]["_output"]
  //       : k extends keyof Output
  //       ? Output[k]
  //       : never;
  //   },
  //   NewInput extends {
  //     [k in keyof Augmentation | keyof Input]: k extends keyof Augmentation
  //       ? Augmentation[k]["_input"]
  //       : k extends keyof Input
  //       ? Input[k]
  //       : never;
  //   }
  // >(
  //   merging: Incoming
  // ): ZodObject<
  //   extendShape<T, ReturnType<Incoming["_def"]["shape"]>>,
  //   Incoming["_def"]["unknownKeys"],
  //   Incoming["_def"]["catchall"],
  //   NewOutput,
  //   NewInput
  // > {
  //   const merged: any = new ZodObject({
  //     unknownKeys: merging._def.unknownKeys,
  //     catchall: merging._def.catchall,
  //     shape: () =>
  //       objectUtil.mergeShapes(this._def.shape(), merging._def.shape()),
  //     typeName: ZodFirstPartyTypeKind.ZodObject,
  //   }) as any;
  //   return merged;
  // }
  setKey(t, n) {
    return this.augment({ [t]: n });
  }
  // merge<Incoming extends AnyZodObject>(
  //   merging: Incoming
  // ): //ZodObject<T & Incoming["_shape"], UnknownKeys, Catchall> = (merging) => {
  // ZodObject<
  //   extendShape<T, ReturnType<Incoming["_def"]["shape"]>>,
  //   Incoming["_def"]["unknownKeys"],
  //   Incoming["_def"]["catchall"]
  // > {
  //   // const mergedShape = objectUtil.mergeShapes(
  //   //   this._def.shape(),
  //   //   merging._def.shape()
  //   // );
  //   const merged: any = new ZodObject({
  //     unknownKeys: merging._def.unknownKeys,
  //     catchall: merging._def.catchall,
  //     shape: () =>
  //       objectUtil.mergeShapes(this._def.shape(), merging._def.shape()),
  //     typeName: ZodFirstPartyTypeKind.ZodObject,
  //   }) as any;
  //   return merged;
  // }
  catchall(t) {
    return new j({
      ...this._def,
      catchall: t
    });
  }
  pick(t) {
    const n = {};
    for (const d of R.objectKeys(t))
      t[d] && this.shape[d] && (n[d] = this.shape[d]);
    return new j({
      ...this._def,
      shape: () => n
    });
  }
  omit(t) {
    const n = {};
    for (const d of R.objectKeys(this.shape))
      t[d] || (n[d] = this.shape[d]);
    return new j({
      ...this._def,
      shape: () => n
    });
  }
  /**
   * @deprecated
   */
  deepPartial() {
    return P(this);
  }
  partial(t) {
    const n = {};
    for (const d of R.objectKeys(this.shape)) {
      const u = this.shape[d];
      t && !t[d] ? n[d] = u : n[d] = u.optional();
    }
    return new j({
      ...this._def,
      shape: () => n
    });
  }
  required(t) {
    const n = {};
    for (const d of R.objectKeys(this.shape))
      if (t && !t[d])
        n[d] = this.shape[d];
      else {
        let m = this.shape[d];
        for (; m instanceof V; )
          m = m._def.innerType;
        n[d] = m;
      }
    return new j({
      ...this._def,
      shape: () => n
    });
  }
  keyof() {
    return Se(R.objectKeys(this.shape));
  }
}
j.create = (i, t) => new j({
  shape: () => i,
  unknownKeys: "strip",
  catchall: L.create(),
  typeName: w.ZodObject,
  ...O(t)
});
j.strictCreate = (i, t) => new j({
  shape: () => i,
  unknownKeys: "strict",
  catchall: L.create(),
  typeName: w.ZodObject,
  ...O(t)
});
j.lazycreate = (i, t) => new j({
  shape: i,
  unknownKeys: "strip",
  catchall: L.create(),
  typeName: w.ZodObject,
  ...O(t)
});
class K extends A {
  _parse(t) {
    const { ctx: n } = this._processInputParams(t), d = this._def.options;
    function u(m) {
      for (const f of m)
        if (f.result.status === "valid")
          return f.result;
      for (const f of m)
        if (f.result.status === "dirty")
          return n.common.issues.push(...f.ctx.common.issues), f.result;
      const _ = m.map((f) => new z(f.ctx.common.issues));
      return h(n, {
        code: p.invalid_union,
        unionErrors: _
      }), q;
    }
    if (n.common.async)
      return Promise.all(d.map(async (m) => {
        const _ = {
          ...n,
          common: {
            ...n.common,
            issues: []
          },
          parent: null
        };
        return {
          result: await m._parseAsync({
            data: n.data,
            path: n.path,
            parent: _
          }),
          ctx: _
        };
      })).then(u);
    {
      let m;
      const _ = [];
      for (const x of d) {
        const k = {
          ...n,
          common: {
            ...n.common,
            issues: []
          },
          parent: null
        }, S = x._parseSync({
          data: n.data,
          path: n.path,
          parent: k
        });
        if (S.status === "valid")
          return S;
        S.status === "dirty" && !m && (m = { result: S, ctx: k }), k.common.issues.length && _.push(k.common.issues);
      }
      if (m)
        return n.common.issues.push(...m.ctx.common.issues), m.result;
      const f = _.map((x) => new z(x));
      return h(n, {
        code: p.invalid_union,
        unionErrors: f
      }), q;
    }
  }
  get options() {
    return this._def.options;
  }
}
K.create = (i, t) => new K({
  options: i,
  typeName: w.ZodUnion,
  ...O(t)
});
function oe(i, t) {
  const n = I(i), d = I(t);
  if (i === t)
    return { valid: !0, data: i };
  if (n === v.object && d === v.object) {
    const u = R.objectKeys(t), m = R.objectKeys(i).filter((f) => u.indexOf(f) !== -1), _ = { ...i, ...t };
    for (const f of m) {
      const x = oe(i[f], t[f]);
      if (!x.valid)
        return { valid: !1 };
      _[f] = x.data;
    }
    return { valid: !0, data: _ };
  } else if (n === v.array && d === v.array) {
    if (i.length !== t.length)
      return { valid: !1 };
    const u = [];
    for (let m = 0; m < i.length; m++) {
      const _ = i[m], f = t[m], x = oe(_, f);
      if (!x.valid)
        return { valid: !1 };
      u.push(x.data);
    }
    return { valid: !0, data: u };
  } else return n === v.date && d === v.date && +i == +t ? { valid: !0, data: i } : { valid: !1 };
}
class ee extends A {
  _parse(t) {
    const { status: n, ctx: d } = this._processInputParams(t), u = (m, _) => {
      if (fe(m) || fe(_))
        return q;
      const f = oe(m.value, _.value);
      return f.valid ? ((pe(m) || pe(_)) && n.dirty(), { status: n.value, value: f.data }) : (h(d, {
        code: p.invalid_intersection_types
      }), q);
    };
    return d.common.async ? Promise.all([
      this._def.left._parseAsync({
        data: d.data,
        path: d.path,
        parent: d
      }),
      this._def.right._parseAsync({
        data: d.data,
        path: d.path,
        parent: d
      })
    ]).then(([m, _]) => u(m, _)) : u(this._def.left._parseSync({
      data: d.data,
      path: d.path,
      parent: d
    }), this._def.right._parseSync({
      data: d.data,
      path: d.path,
      parent: d
    }));
  }
}
ee.create = (i, t, n) => new ee({
  left: i,
  right: t,
  typeName: w.ZodIntersection,
  ...O(n)
});
class M extends A {
  _parse(t) {
    const { status: n, ctx: d } = this._processInputParams(t);
    if (d.parsedType !== v.array)
      return h(d, {
        code: p.invalid_type,
        expected: v.array,
        received: d.parsedType
      }), q;
    if (d.data.length < this._def.items.length)
      return h(d, {
        code: p.too_small,
        minimum: this._def.items.length,
        inclusive: !0,
        exact: !1,
        type: "array"
      }), q;
    !this._def.rest && d.data.length > this._def.items.length && (h(d, {
      code: p.too_big,
      maximum: this._def.items.length,
      inclusive: !0,
      exact: !1,
      type: "array"
    }), n.dirty());
    const m = [...d.data].map((_, f) => {
      const x = this._def.items[f] || this._def.rest;
      return x ? x._parse(new N(d, _, d.path, f)) : null;
    }).filter((_) => !!_);
    return d.common.async ? Promise.all(m).then((_) => T.mergeArray(n, _)) : T.mergeArray(n, m);
  }
  get items() {
    return this._def.items;
  }
  rest(t) {
    return new M({
      ...this._def,
      rest: t
    });
  }
}
M.create = (i, t) => {
  if (!Array.isArray(i))
    throw new Error("You must pass an array of schemas to z.tuple([ ... ])");
  return new M({
    items: i,
    typeName: w.ZodTuple,
    rest: null,
    ...O(t)
  });
};
class te extends A {
  get keySchema() {
    return this._def.keyType;
  }
  get valueSchema() {
    return this._def.valueType;
  }
  _parse(t) {
    const { status: n, ctx: d } = this._processInputParams(t);
    if (d.parsedType !== v.object)
      return h(d, {
        code: p.invalid_type,
        expected: v.object,
        received: d.parsedType
      }), q;
    const u = [], m = this._def.keyType, _ = this._def.valueType;
    for (const f in d.data)
      u.push({
        key: m._parse(new N(d, f, d.path, f)),
        value: _._parse(new N(d, d.data[f], d.path, f)),
        alwaysSet: f in d.data
      });
    return d.common.async ? T.mergeObjectAsync(n, u) : T.mergeObjectSync(n, u);
  }
  get element() {
    return this._def.valueType;
  }
  static create(t, n, d) {
    return n instanceof A ? new te({
      keyType: t,
      valueType: n,
      typeName: w.ZodRecord,
      ...O(d)
    }) : new te({
      keyType: Z.create(),
      valueType: t,
      typeName: w.ZodRecord,
      ...O(n)
    });
  }
}
class ke extends A {
  get keySchema() {
    return this._def.keyType;
  }
  get valueSchema() {
    return this._def.valueType;
  }
  _parse(t) {
    const { status: n, ctx: d } = this._processInputParams(t);
    if (d.parsedType !== v.map)
      return h(d, {
        code: p.invalid_type,
        expected: v.map,
        received: d.parsedType
      }), q;
    const u = this._def.keyType, m = this._def.valueType, _ = [...d.data.entries()].map(([f, x], k) => ({
      key: u._parse(new N(d, f, d.path, [k, "key"])),
      value: m._parse(new N(d, x, d.path, [k, "value"]))
    }));
    if (d.common.async) {
      const f = /* @__PURE__ */ new Map();
      return Promise.resolve().then(async () => {
        for (const x of _) {
          const k = await x.key, S = await x.value;
          if (k.status === "aborted" || S.status === "aborted")
            return q;
          (k.status === "dirty" || S.status === "dirty") && n.dirty(), f.set(k.value, S.value);
        }
        return { status: n.value, value: f };
      });
    } else {
      const f = /* @__PURE__ */ new Map();
      for (const x of _) {
        const k = x.key, S = x.value;
        if (k.status === "aborted" || S.status === "aborted")
          return q;
        (k.status === "dirty" || S.status === "dirty") && n.dirty(), f.set(k.value, S.value);
      }
      return { status: n.value, value: f };
    }
  }
}
ke.create = (i, t, n) => new ke({
  valueType: t,
  keyType: i,
  typeName: w.ZodMap,
  ...O(n)
});
class Q extends A {
  _parse(t) {
    const { status: n, ctx: d } = this._processInputParams(t);
    if (d.parsedType !== v.set)
      return h(d, {
        code: p.invalid_type,
        expected: v.set,
        received: d.parsedType
      }), q;
    const u = this._def;
    u.minSize !== null && d.data.size < u.minSize.value && (h(d, {
      code: p.too_small,
      minimum: u.minSize.value,
      type: "set",
      inclusive: !0,
      exact: !1,
      message: u.minSize.message
    }), n.dirty()), u.maxSize !== null && d.data.size > u.maxSize.value && (h(d, {
      code: p.too_big,
      maximum: u.maxSize.value,
      type: "set",
      inclusive: !0,
      exact: !1,
      message: u.maxSize.message
    }), n.dirty());
    const m = this._def.valueType;
    function _(x) {
      const k = /* @__PURE__ */ new Set();
      for (const S of x) {
        if (S.status === "aborted")
          return q;
        S.status === "dirty" && n.dirty(), k.add(S.value);
      }
      return { status: n.value, value: k };
    }
    const f = [...d.data.values()].map((x, k) => m._parse(new N(d, x, d.path, k)));
    return d.common.async ? Promise.all(f).then((x) => _(x)) : _(f);
  }
  min(t, n) {
    return new Q({
      ...this._def,
      minSize: { value: t, message: y.toString(n) }
    });
  }
  max(t, n) {
    return new Q({
      ...this._def,
      maxSize: { value: t, message: y.toString(n) }
    });
  }
  size(t, n) {
    return this.min(t, n).max(t, n);
  }
  nonempty(t) {
    return this.min(1, t);
  }
}
Q.create = (i, t) => new Q({
  valueType: i,
  minSize: null,
  maxSize: null,
  typeName: w.ZodSet,
  ...O(t)
});
class we extends A {
  get schema() {
    return this._def.getter();
  }
  _parse(t) {
    const { ctx: n } = this._processInputParams(t);
    return this._def.getter()._parse({ data: n.data, path: n.path, parent: n });
  }
}
we.create = (i, t) => new we({
  getter: i,
  typeName: w.ZodLazy,
  ...O(t)
});
class de extends A {
  _parse(t) {
    if (t.data !== this._def.value) {
      const n = this._getOrReturnCtx(t);
      return h(n, {
        received: n.data,
        code: p.invalid_literal,
        expected: this._def.value
      }), q;
    }
    return { status: "valid", value: t.data };
  }
  get value() {
    return this._def.value;
  }
}
de.create = (i, t) => new de({
  value: i,
  typeName: w.ZodLiteral,
  ...O(t)
});
function Se(i, t) {
  return new U({
    values: i,
    typeName: w.ZodEnum,
    ...O(t)
  });
}
class U extends A {
  _parse(t) {
    if (typeof t.data != "string") {
      const n = this._getOrReturnCtx(t), d = this._def.values;
      return h(n, {
        expected: R.joinValues(d),
        received: n.parsedType,
        code: p.invalid_type
      }), q;
    }
    if (this._cache || (this._cache = new Set(this._def.values)), !this._cache.has(t.data)) {
      const n = this._getOrReturnCtx(t), d = this._def.values;
      return h(n, {
        received: n.data,
        code: p.invalid_enum_value,
        options: d
      }), q;
    }
    return C(t.data);
  }
  get options() {
    return this._def.values;
  }
  get enum() {
    const t = {};
    for (const n of this._def.values)
      t[n] = n;
    return t;
  }
  get Values() {
    const t = {};
    for (const n of this._def.values)
      t[n] = n;
    return t;
  }
  get Enum() {
    const t = {};
    for (const n of this._def.values)
      t[n] = n;
    return t;
  }
  extract(t, n = this._def) {
    return U.create(t, {
      ...this._def,
      ...n
    });
  }
  exclude(t, n = this._def) {
    return U.create(this.options.filter((d) => !t.includes(d)), {
      ...this._def,
      ...n
    });
  }
}
U.create = Se;
class qe extends A {
  _parse(t) {
    const n = R.getValidEnumValues(this._def.values), d = this._getOrReturnCtx(t);
    if (d.parsedType !== v.string && d.parsedType !== v.number) {
      const u = R.objectValues(n);
      return h(d, {
        expected: R.joinValues(u),
        received: d.parsedType,
        code: p.invalid_type
      }), q;
    }
    if (this._cache || (this._cache = new Set(R.getValidEnumValues(this._def.values))), !this._cache.has(t.data)) {
      const u = R.objectValues(n);
      return h(d, {
        received: d.data,
        code: p.invalid_enum_value,
        options: u
      }), q;
    }
    return C(t.data);
  }
  get enum() {
    return this._def.values;
  }
}
qe.create = (i, t) => new qe({
  values: i,
  typeName: w.ZodNativeEnum,
  ...O(t)
});
class ie extends A {
  unwrap() {
    return this._def.type;
  }
  _parse(t) {
    const { ctx: n } = this._processInputParams(t);
    if (n.parsedType !== v.promise && n.common.async === !1)
      return h(n, {
        code: p.invalid_type,
        expected: v.promise,
        received: n.parsedType
      }), q;
    const d = n.parsedType === v.promise ? n.data : Promise.resolve(n.data);
    return C(d.then((u) => this._def.type.parseAsync(u, {
      path: n.path,
      errorMap: n.common.contextualErrorMap
    })));
  }
}
ie.create = (i, t) => new ie({
  type: i,
  typeName: w.ZodPromise,
  ...O(t)
});
class F extends A {
  innerType() {
    return this._def.schema;
  }
  sourceType() {
    return this._def.schema._def.typeName === w.ZodEffects ? this._def.schema.sourceType() : this._def.schema;
  }
  _parse(t) {
    const { status: n, ctx: d } = this._processInputParams(t), u = this._def.effect || null, m = {
      addIssue: (_) => {
        h(d, _), _.fatal ? n.abort() : n.dirty();
      },
      get path() {
        return d.path;
      }
    };
    if (m.addIssue = m.addIssue.bind(m), u.type === "preprocess") {
      const _ = u.transform(d.data, m);
      if (d.common.async)
        return Promise.resolve(_).then(async (f) => {
          if (n.value === "aborted")
            return q;
          const x = await this._def.schema._parseAsync({
            data: f,
            path: d.path,
            parent: d
          });
          return x.status === "aborted" ? q : x.status === "dirty" || n.value === "dirty" ? H(x.value) : x;
        });
      {
        if (n.value === "aborted")
          return q;
        const f = this._def.schema._parseSync({
          data: _,
          path: d.path,
          parent: d
        });
        return f.status === "aborted" ? q : f.status === "dirty" || n.value === "dirty" ? H(f.value) : f;
      }
    }
    if (u.type === "refinement") {
      const _ = (f) => {
        const x = u.refinement(f, m);
        if (d.common.async)
          return Promise.resolve(x);
        if (x instanceof Promise)
          throw new Error("Async refinement encountered during synchronous parse operation. Use .parseAsync instead.");
        return f;
      };
      if (d.common.async === !1) {
        const f = this._def.schema._parseSync({
          data: d.data,
          path: d.path,
          parent: d
        });
        return f.status === "aborted" ? q : (f.status === "dirty" && n.dirty(), _(f.value), { status: n.value, value: f.value });
      } else
        return this._def.schema._parseAsync({ data: d.data, path: d.path, parent: d }).then((f) => f.status === "aborted" ? q : (f.status === "dirty" && n.dirty(), _(f.value).then(() => ({ status: n.value, value: f.value }))));
    }
    if (u.type === "transform")
      if (d.common.async === !1) {
        const _ = this._def.schema._parseSync({
          data: d.data,
          path: d.path,
          parent: d
        });
        if (!D(_))
          return q;
        const f = u.transform(_.value, m);
        if (f instanceof Promise)
          throw new Error("Asynchronous transform encountered during synchronous parse operation. Use .parseAsync instead.");
        return { status: n.value, value: f };
      } else
        return this._def.schema._parseAsync({ data: d.data, path: d.path, parent: d }).then((_) => D(_) ? Promise.resolve(u.transform(_.value, m)).then((f) => ({
          status: n.value,
          value: f
        })) : q);
    R.assertNever(u);
  }
}
F.create = (i, t, n) => new F({
  schema: i,
  typeName: w.ZodEffects,
  effect: t,
  ...O(n)
});
F.createWithPreprocess = (i, t, n) => new F({
  schema: t,
  effect: { type: "preprocess", transform: i },
  typeName: w.ZodEffects,
  ...O(n)
});
class V extends A {
  _parse(t) {
    return this._getType(t) === v.undefined ? C(void 0) : this._def.innerType._parse(t);
  }
  unwrap() {
    return this._def.innerType;
  }
}
V.create = (i, t) => new V({
  innerType: i,
  typeName: w.ZodOptional,
  ...O(t)
});
class W extends A {
  _parse(t) {
    return this._getType(t) === v.null ? C(null) : this._def.innerType._parse(t);
  }
  unwrap() {
    return this._def.innerType;
  }
}
W.create = (i, t) => new W({
  innerType: i,
  typeName: w.ZodNullable,
  ...O(t)
});
class ce extends A {
  _parse(t) {
    const { ctx: n } = this._processInputParams(t);
    let d = n.data;
    return n.parsedType === v.undefined && (d = this._def.defaultValue()), this._def.innerType._parse({
      data: d,
      path: n.path,
      parent: n
    });
  }
  removeDefault() {
    return this._def.innerType;
  }
}
ce.create = (i, t) => new ce({
  innerType: i,
  typeName: w.ZodDefault,
  defaultValue: typeof t.default == "function" ? t.default : () => t.default,
  ...O(t)
});
class ue extends A {
  _parse(t) {
    const { ctx: n } = this._processInputParams(t), d = {
      ...n,
      common: {
        ...n.common,
        issues: []
      }
    }, u = this._def.innerType._parse({
      data: d.data,
      path: d.path,
      parent: {
        ...d
      }
    });
    return G(u) ? u.then((m) => ({
      status: "valid",
      value: m.status === "valid" ? m.value : this._def.catchValue({
        get error() {
          return new z(d.common.issues);
        },
        input: d.data
      })
    })) : {
      status: "valid",
      value: u.status === "valid" ? u.value : this._def.catchValue({
        get error() {
          return new z(d.common.issues);
        },
        input: d.data
      })
    };
  }
  removeCatch() {
    return this._def.innerType;
  }
}
ue.create = (i, t) => new ue({
  innerType: i,
  typeName: w.ZodCatch,
  catchValue: typeof t.catch == "function" ? t.catch : () => t.catch,
  ...O(t)
});
class Oe extends A {
  _parse(t) {
    if (this._getType(t) !== v.nan) {
      const d = this._getOrReturnCtx(t);
      return h(d, {
        code: p.invalid_type,
        expected: v.nan,
        received: d.parsedType
      }), q;
    }
    return { status: "valid", value: t.data };
  }
}
Oe.create = (i) => new Oe({
  typeName: w.ZodNaN,
  ...O(i)
});
class nt extends A {
  _parse(t) {
    const { ctx: n } = this._processInputParams(t), d = n.data;
    return this._def.type._parse({
      data: d,
      path: n.path,
      parent: n
    });
  }
  unwrap() {
    return this._def.type;
  }
}
class me extends A {
  _parse(t) {
    const { status: n, ctx: d } = this._processInputParams(t);
    if (d.common.async)
      return (async () => {
        const m = await this._def.in._parseAsync({
          data: d.data,
          path: d.path,
          parent: d
        });
        return m.status === "aborted" ? q : m.status === "dirty" ? (n.dirty(), H(m.value)) : this._def.out._parseAsync({
          data: m.value,
          path: d.path,
          parent: d
        });
      })();
    {
      const u = this._def.in._parseSync({
        data: d.data,
        path: d.path,
        parent: d
      });
      return u.status === "aborted" ? q : u.status === "dirty" ? (n.dirty(), {
        status: "dirty",
        value: u.value
      }) : this._def.out._parseSync({
        data: u.value,
        path: d.path,
        parent: d
      });
    }
  }
  static create(t, n) {
    return new me({
      in: t,
      out: n,
      typeName: w.ZodPipeline
    });
  }
}
class le extends A {
  _parse(t) {
    const n = this._def.innerType._parse(t), d = (u) => (D(u) && (u.value = Object.freeze(u.value)), u);
    return G(n) ? n.then((u) => d(u)) : d(n);
  }
  unwrap() {
    return this._def.innerType;
  }
}
le.create = (i, t) => new le({
  innerType: i,
  typeName: w.ZodReadonly,
  ...O(t)
});
var w;
(function(i) {
  i.ZodString = "ZodString", i.ZodNumber = "ZodNumber", i.ZodNaN = "ZodNaN", i.ZodBigInt = "ZodBigInt", i.ZodBoolean = "ZodBoolean", i.ZodDate = "ZodDate", i.ZodSymbol = "ZodSymbol", i.ZodUndefined = "ZodUndefined", i.ZodNull = "ZodNull", i.ZodAny = "ZodAny", i.ZodUnknown = "ZodUnknown", i.ZodNever = "ZodNever", i.ZodVoid = "ZodVoid", i.ZodArray = "ZodArray", i.ZodObject = "ZodObject", i.ZodUnion = "ZodUnion", i.ZodDiscriminatedUnion = "ZodDiscriminatedUnion", i.ZodIntersection = "ZodIntersection", i.ZodTuple = "ZodTuple", i.ZodRecord = "ZodRecord", i.ZodMap = "ZodMap", i.ZodSet = "ZodSet", i.ZodFunction = "ZodFunction", i.ZodLazy = "ZodLazy", i.ZodLiteral = "ZodLiteral", i.ZodEnum = "ZodEnum", i.ZodEffects = "ZodEffects", i.ZodNativeEnum = "ZodNativeEnum", i.ZodOptional = "ZodOptional", i.ZodNullable = "ZodNullable", i.ZodDefault = "ZodDefault", i.ZodCatch = "ZodCatch", i.ZodPromise = "ZodPromise", i.ZodBranded = "ZodBranded", i.ZodPipeline = "ZodPipeline", i.ZodReadonly = "ZodReadonly";
})(w || (w = {}));
const e = Z.create, r = B.create, b = ae.create, a = re.create;
L.create;
const o = $.create, l = j.create, s = K.create;
ee.create;
M.create;
const E = te.create, g = de.create, c = U.create;
ie.create;
V.create;
W.create;
l({ code: e().min(1).max(128), message: e().min(1).max(4e3), retryable: b() }).strict();
const ht = l({ schema_version: g("dsh-browser-status.v1"), ready: b(), runtime_mode: c(["live", "replay"]), interaction_mode: c(["interactive", "read_only"]), run_id: s([e().min(1).max(128), a()]), dsh_session_id: s([e().min(1).max(256), a()]), state: s([c(["admitted", "running", "idle", "completed", "failed", "cancelled", "unknown"]), a()]), error_code: s([e().max(128), a()]), decision_desk_url: e().url().max(2048), business: s([l({ schema_version: g("dsh-business-status.v1"), status: c(["queued", "researching", "completed", "degraded", "research_only", "rejected", "failed", "cancelled"]), gate_status: s([g("publish"), g("degraded"), g("research_only"), g("reject"), g(null)]), coverage_status: s([g("insufficient"), g("sufficient"), g("bounded_stop"), g(null)]), hard_coverage_ratio: s([r().gte(0).lte(1), a()]), stop_reason_code: s([e().max(128), a()]), stop_reason_detail: s([e().max(2e3), a()]), failures: o(l({ capability_id: e().min(1).max(256), error_code: e().min(1).max(128), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e().max(128), a()]), retryable: b() }).strict()).max(16) }).strict(), a()]) }).strict();
l({ capability_id: e().min(1).max(256), error_code: e().min(1).max(128), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e().max(128), a()]), retryable: b() }).strict();
const gt = l({ schema_version: g("dsh-business-status.v1"), status: c(["queued", "researching", "completed", "degraded", "research_only", "rejected", "failed", "cancelled"]), gate_status: s([g("publish"), g("degraded"), g("research_only"), g("reject"), g(null)]), coverage_status: s([g("insufficient"), g("sufficient"), g("bounded_stop"), g(null)]), hard_coverage_ratio: s([r().gte(0).lte(1), a()]), stop_reason_code: s([e().max(128), a()]), stop_reason_detail: s([e().max(2e3), a()]), failures: o(l({ capability_id: e().min(1).max(256), error_code: e().min(1).max(128), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e().max(128), a()]), retryable: b() }).strict()).max(16) }).strict(), vt = l({ schema_version: g("dsh-host-readiness.v1"), ready: b(), version_compatible: b(), session_controller: b(), client_plugin: b(), hub_reachable: b(), upstream_identity: l({ source_commit: e().regex(new RegExp("^[a-f0-9]{40}$")), source_version: e().min(1).max(128), package_versions: E(e().min(1).max(128)), plugin_build_hash: e().regex(new RegExp("^[a-f0-9]{64}$")) }).strict(), checked_at: e().datetime({ offset: !0 }), error_code: s([e().max(128), a()]) }).strict(), yt = l({ schema_version: g("dsh-research-intake.v1"), text: e().min(1).max(2e5), source_id: e().min(1).max(128), language: e().min(2).max(16), client_session_id: s([e().max(256), a()]) }).strict(), bt = l({ schema_version: g("dsh-research-intake-accepted.v1"), event_id: e().min(1).max(128), run_id: e().min(1).max(128), status: g("queued"), status_url: e().min(1).max(2048), decision_desk_url: e().url().max(2048) }).strict(), xt = l({ schema_version: g("dsh-run-session-link.v1"), run_id: e().min(1).max(128), dsh_session_id: e().min(1).max(256), request_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), state: c(["admitted", "running", "idle", "completed", "failed", "cancelled", "unknown"]), generation: r().int().gte(1), upstream_identity: l({ source_commit: e().regex(new RegExp("^[a-f0-9]{40}$")), source_version: e().min(1).max(128), package_versions: E(e().min(1).max(128)), plugin_build_hash: e().regex(new RegExp("^[a-f0-9]{64}$")) }).strict(), deadline_at: s([e().datetime({ offset: !0 }), a()]), max_tool_calls: s([r().int().gte(1).lte(100), a()]), tool_calls_started: r().int().gte(0), accepted_at: s([e().datetime({ offset: !0 }), a()]), last_seen_at: s([e().datetime({ offset: !0 }), a()]), terminal_at: s([e().datetime({ offset: !0 }), a()]), last_seq: r().int().gte(0), trace_ref: s([e().max(2048), a()]), result_ref: s([e().max(2048), a()]), result_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), error_code: s([e().max(128), a()]), created_at: e().datetime({ offset: !0 }), updated_at: e().datetime({ offset: !0 }) }).strict(), kt = l({ schema_version: g("dsh-session-accepted.v1"), run_id: e().min(1).max(128), dsh_session_id: e().min(1).max(256), accepted_at: e().datetime({ offset: !0 }), generation: r().int().gte(1) }).strict(), wt = l({ schema_version: g("dsh-session-completion.v1"), run_id: e().min(1).max(128), dsh_session_id: e().min(1).max(256), terminal_status: c(["completed", "failed", "cancelled"]), generation: r().int().gte(1), last_seq: r().int().gte(0), trace_ref: s([e().max(2048), a()]), result_ref: s([e().max(2048), a()]), result_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), completed_at: e().datetime({ offset: !0 }), error: s([l({ code: e().min(1).max(128), message: e().min(1).max(4e3), retryable: b() }).strict(), a()]) }).strict(), qt = l({ schema_version: g("dsh-session-prompt.v1"), run_id: e().min(1).max(128), dsh_session_id: e().min(1).max(256), request_id: e().min(1).max(256), request_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), generation: r().int().gte(1), prompt: e().min(1).max(1048576) }).strict(), Ot = l({ schema_version: g("dsh-session-result.v1"), run_id: e().min(1).max(128), dsh_session_id: e().min(1).max(256), generation: r().int().gte(1), last_seq: r().int().gte(0), final_response: e().max(1048576), finish_reason: s([e().max(128), a()]), events_json: e().min(2).max(8388608), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), trace_ref: e().min(1).max(2048), result_hash: e().regex(new RegExp("^[a-f0-9]{64}$")) }).strict(), At = l({ schema_version: g("dsh-session-status.v1"), run_id: e().min(1).max(128), dsh_session_id: e().min(1).max(256), state: c(["admitted", "running", "idle", "completed", "failed", "cancelled", "unknown"]), generation: r().int().gte(1), last_seq: r().int().gte(0), observed_at: e().datetime({ offset: !0 }), error_code: s([e().max(128), a()]) }).strict(), Rt = l({ schema_version: g("dsh-session-submit.v1"), run_id: e().min(1).max(128), request_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), deterministic_session_id: e().min(1).max(256), deterministic_request_id: e().min(1).max(256), workspace_ref: e().min(1).max(2048), prompt_ref: e().min(1).max(2048), agent_preset: s([e().max(256), a()]), permission_ref: e().min(1).max(2048), deadline_at: e().datetime({ offset: !0 }), model_step_timeout_ms: r().int().gte(1e3).lte(6e5), max_tool_calls: r().int().gte(1).lte(100), generation: r().int().gte(1) }).strict();
l({ source_commit: e().regex(new RegExp("^[a-f0-9]{40}$")), source_version: e().min(1).max(128), package_versions: E(e().min(1).max(128)), plugin_build_hash: e().regex(new RegExp("^[a-f0-9]{64}$")) }).strict();
l({ case_id: e().min(1), thesis: e().min(1), main_chain: o(l({ link_id: e().min(1), claim_type: c(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(c(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), opposite_chain: o(l({ link_id: e().min(1), claim_type: c(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(c(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), unresolved_questions: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict();
l({ link_id: e().min(1), claim_type: c(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(c(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict();
l({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: c(["hard", "soft"]), summary: e().min(1), resolution_status: c(["open", "resolved", "bounded_stop"]) }).strict();
l({ status: c(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(l({ requirement_id: e().min(1), importance: c(["hard", "soft"]), reason_code: c(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: b() }).strict()), conflicts: o(l({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: c(["hard", "soft"]), summary: e().min(1), resolution_status: c(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict();
l({ schema_version: g("domain-pack-manifest.v1"), pack_id: e().regex(new RegExp("^[a-z][a-z0-9_-]*$")), version: e().min(1), product_extension_ref: e().min(1), doctrine_ref: e().min(1), evidence_policy_ref: e().min(1), gate_policy_ref: e().min(1), evaluation_policy_ref: e().min(1), role_profile_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), capability_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), horizons: o(c(["30m", "24h", "72h"])).min(3).max(3).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), execution_budget: l({ max_evidence_rounds: r().int().gte(1).lte(10), max_tool_calls: r().int().gte(1).lte(100), max_subagents: r().int().gte(0).lte(32), total_deadline_seconds: r().int().gte(1).lte(3600), per_tool_timeout_seconds: r().int().gte(1).lte(600), per_model_step_timeout_seconds: r().int().gte(1).lte(600), max_structured_repairs: r().int().gte(0).lte(5), max_estimated_cost_usd: r().gte(0) }).strict() }).strict();
const St = l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict();
l({ evidence_id: e().min(1), requirement_id: e().min(1), kind: c(["official", "market", "web", "transcript", "document"]), authority: c(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3), structured_payload_ref: s([e(), a()]), tool_call_id: s([e(), a()]), research_session_id: e().min(1), round: r().int().gte(1), quality: c(["candidate", "accepted", "rejected", "stale", "conflicted"]), freshness_status: c(["fresh", "stale", "unknown"]), conflict_group: s([e(), a()]) }).strict();
l({ requirement_id: e().min(1), importance: c(["hard", "soft"]), reason_code: c(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: b() }).strict();
l({ requirement_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), description: e().min(1), importance: c(["hard", "soft"]), source_priority: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), authority_floor: c(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), preferred_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), freshness_seconds: r().int().gte(0), minimum_independent_sources: r().int().gte(1).lte(10), allowed_fallbacks: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap: r().gte(0).lte(1) }).strict();
l({ max_evidence_rounds: r().int().gte(1).lte(10), max_tool_calls: r().int().gte(1).lte(100), max_subagents: r().int().gte(0).lte(32), total_deadline_seconds: r().int().gte(1).lte(3600), per_tool_timeout_seconds: r().int().gte(1).lte(600), per_model_step_timeout_seconds: r().int().gte(1).lte(600), max_structured_repairs: r().int().gte(0).lte(5), max_estimated_cost_usd: r().gte(0) }).strict();
l({ horizon: c(["30m", "24h", "72h"]), action: c(["long", "short", "neutral", "no_trade"]), subjective_probability: r().gte(0).lte(1), probability_status: c(["uncalibrated", "calibrated"]), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), trigger: e().min(1), invalidation: e().min(1), expires_at: e().datetime({ offset: !0 }), next_review_at: e().datetime({ offset: !0 }), missing_facts: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap_reason: s([e(), a()]) }).strict();
l({ schema_version: g("product-extension-manifest.v1"), extension_id: e().regex(new RegExp("^[a-z][a-z0-9_-]*$")), version: e().min(1), task_schema_ref: e().min(1), artifact_schema_ref: e().min(1), evaluation_policy_ref: e().min(1), domain_pack_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), dsh_bundle_ref: s([e(), a()]) }).strict();
l({ schema_version: g("research-capability-manifest.v1"), capability_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), version: e().min(1), kind: c(["dsh_plugin", "mcp", "provider", "python_adapter", "replay"]), implementation_ref: e().min(1), input_schema_ref: e().min(1), output_schema_ref: e().min(1), permissions: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), allowed_domains: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), timeout_seconds: r().int().gte(1).lte(600), cost_policy_ref: e().min(1), freshness_policy_ref: e().min(1), license_status: c(["approved", "review_required", "denied"]), audit_status: c(["approved", "candidate", "denied"]), replay_policy: c(["archive_required", "deterministic", "unavailable"]), secret_policy: c(["none", "adapter_only", "local_secret_store"]) }).strict();
const jt = l({ schema_version: g("research-capability-query.v1"), request_id: e().min(1), capability_id: e().min(1), requirement_id: e().min(1), query: e().min(1).max(2e3), target_url: s([e().url(), a()]), symbols: o(e().min(1)).max(20).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), fields: o(e().min(1)).max(50).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), allowed_domains: o(e().min(1)).max(100).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), max_results: r().int().gte(1).lte(20), max_cost_usd: s([r().gte(0), a()]), research_session_id: e().min(1), round: r().int().gte(1), mode: c(["live", "replay"]), observed_at: e().datetime({ offset: !0 }), requested_observed_at: s([e().datetime({ offset: !0 }), a()]).optional(), cutoff_at: e().datetime({ offset: !0 }) }).strict(), Tt = l({ schema_version: g("research-capability-result.v1"), request_id: e().min(1), capability_id: e().min(1), provider: e().min(1), evidence_candidates: o(l({ evidence_id: e().min(1), requirement_id: e().min(1), kind: c(["official", "market", "web", "transcript", "document"]), authority: c(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3), structured_payload_ref: s([e(), a()]), tool_call_id: s([e(), a()]), research_session_id: e().min(1), round: r().int().gte(1), quality: c(["candidate", "accepted", "rejected", "stale", "conflicted"]), freshness_status: c(["fresh", "stale", "unknown"]), conflict_group: s([e(), a()]) }).strict()).max(20), cost_usd: s([r().gte(0), a()]), completed_at: e().datetime({ offset: !0 }) }).strict();
l({ capability_id: e().min(1), requirement_id: e().min(1), query_aliases: o(e().min(1).max(2e3)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), evidence_candidates: o(l({ evidence_id: e().min(1), requirement_id: e().min(1), kind: c(["official", "market", "web", "transcript", "document"]), authority: c(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3), structured_payload_ref: s([e(), a()]), tool_call_id: s([e(), a()]), research_session_id: e().min(1), round: r().int().gte(1), quality: c(["candidate", "accepted", "rejected", "stale", "conflicted"]), freshness_status: c(["fresh", "stale", "unknown"]), conflict_group: s([e(), a()]) }).strict()).min(1).max(20) }).strict();
l({ schema_version: g("research-evaluation-case.v1"), case_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), event_family: c(["central_bank_speech", "monetary_policy_decision", "inflation_release", "labor_release", "geopolitical_shock"]), input_text: e().min(1).max(1e4), trigger_evidence: l({ evidence_id: e().min(1), kind: c(["official", "market", "web", "transcript", "document"]), authority: c(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3) }).strict(), evidence_requirements: o(l({ requirement_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), description: e().min(1), importance: c(["hard", "soft"]), source_priority: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), authority_floor: c(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), preferred_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), freshness_seconds: r().int().gte(0), minimum_independent_sources: r().int().gte(1).lte(10), allowed_fallbacks: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap: r().gte(0).lte(1) }).strict()).min(1), expected_hard_requirement_ids: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), archived_capability_fixtures: o(l({ capability_id: e().min(1), requirement_id: e().min(1), query_aliases: o(e().min(1).max(2e3)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), evidence_candidates: o(l({ evidence_id: e().min(1), requirement_id: e().min(1), kind: c(["official", "market", "web", "transcript", "document"]), authority: c(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3), structured_payload_ref: s([e(), a()]), tool_call_id: s([e(), a()]), research_session_id: e().min(1), round: r().int().gte(1), quality: c(["candidate", "accepted", "rejected", "stale", "conflicted"]), freshness_status: c(["fresh", "stale", "unknown"]), conflict_group: s([e(), a()]) }).strict()).min(1).max(20) }).strict()).min(1), cutoff_at: e().datetime({ offset: !0 }), outcome_available_at: s([e().datetime({ offset: !0 }), a()]), outcome_labels: o(l({ label_id: e().min(1), value: e().min(1), available_at: e().datetime({ offset: !0 }), source_ref: e().min(1) }).strict()), source_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict();
l({ label_id: e().min(1), value: e().min(1), available_at: e().datetime({ offset: !0 }), source_ref: e().min(1) }).strict();
l({ evidence_id: e().min(1), kind: c(["official", "market", "web", "transcript", "document"]), authority: c(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3) }).strict();
l({ plan_id: e().min(1), objective: e().min(1), tasks: o(l({ task_id: e().min(1), capability_id: e().min(1), objective: e().min(1), question: e().min(1), input_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), output_schema_ref: e().min(1), depends_on: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), success_condition: e().min(1), priority: r().int().gte(1).lte(100) }).strict()).min(1), required_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), created_at: e().datetime({ offset: !0 }) }).strict();
l({ round: r().int().gte(1), plan: l({ plan_id: e().min(1), objective: e().min(1), tasks: o(l({ task_id: e().min(1), capability_id: e().min(1), objective: e().min(1), question: e().min(1), input_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), output_schema_ref: e().min(1), depends_on: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), success_condition: e().min(1), priority: r().int().gte(1).lte(100) }).strict()).min(1), required_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), created_at: e().datetime({ offset: !0 }) }).strict(), tool_invocations: o(l({ tool_call_id: e().min(1), capability_id: e().min(1), tool_name: e().min(1), query_summary: e().min(1).max(1e3), started_at: e().datetime({ offset: !0 }), finished_at: s([e().datetime({ offset: !0 }), a()]), status: c(["running", "succeeded", "failed", "denied", "timed_out"]), attempt: r().int().gte(1), latency_ms: s([r().int().gte(0), a()]), cost_usd: s([r().gte(0), a()]), error_code: s([e(), a()]), error: s([l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict(), a()]).optional() }).strict()), tool_results: o(l({ tool_call_id: e().min(1), status: c(["succeeded", "failed", "denied", "timed_out"]), summary: e().min(1).max(2e3), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), content_ref: s([e(), a()]), content_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), received_at: e().datetime({ offset: !0 }), error_code: s([e(), a()]), error: s([l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict(), a()]).optional() }).strict()), new_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), coverage: l({ status: c(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(l({ requirement_id: e().min(1), importance: c(["hard", "soft"]), reason_code: c(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: b() }).strict()), conflicts: o(l({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: c(["hard", "soft"]), summary: e().min(1), resolution_status: c(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }) }).strict();
const Et = l({ schema_version: g("research-run-command.v1"), request_id: e().min(1), command: c(["cancel", "retry", "recheck", "feedback"]), reason: e().min(1) }).strict(), Ct = l({ schema_version: g("research-run-command-result.v1"), request_id: e().min(1), command: c(["cancel", "retry", "recheck", "feedback"]), source_run_id: e().min(1), target_run_id: s([e(), a()]), status: c(["accepted", "already_applied", "rejected"]), created_at: e().datetime({ offset: !0 }) }).strict(), $t = l({ schema_version: g("research-run-detail-view.v1"), run: l({ schema_version: g("research-run-view.v2"), run_id: e().min(1), event_id: e().min(1), event_title: e().min(1), admission_origin: c(["manual", "automatic", "scheduled_recheck", "legacy"]), priority: c(["critical", "high", "normal", "low"]), status: c(["admitted", "queued", "researching", "retry_wait", "completed", "degraded", "research_only", "rejected", "failed", "cancelled"]), stage: c(["admission", "planning", "acquiring_evidence", "assessing_sufficiency", "synthesis", "gate", "monitoring", "done"]), runtime_id: e().min(1), profile_ref: e().min(1), current_round: r().int().gte(0), budget: l({ max_evidence_rounds: r().int().gte(1).lte(10), max_tool_calls: r().int().gte(1).lte(100), max_subagents: r().int().gte(0).lte(32), total_deadline_seconds: r().int().gte(1).lte(3600), per_tool_timeout_seconds: r().int().gte(1).lte(600), per_model_step_timeout_seconds: r().int().gte(1).lte(600), max_structured_repairs: r().int().gte(0).lte(5), max_estimated_cost_usd: r().gte(0) }).strict(), coverage: s([l({ status: c(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(l({ requirement_id: e().min(1), importance: c(["hard", "soft"]), reason_code: c(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: b() }).strict()), conflicts: o(l({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: c(["hard", "soft"]), summary: e().min(1), resolution_status: c(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), a()]), current_action: s([e(), a()]), stop_reason: s([l({ code: c(["sufficient", "deadline", "cost_budget", "tool_budget", "round_budget", "permission_denied", "critical_data_unavailable", "runtime_unavailable", "cancelled"]), detail: e().min(1), bounded: b(), remaining_hard_gaps: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), a()]), failure: s([l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict(), a()]).optional(), latest_sequence_no: r().int().gte(0), artifact_id: s([e(), a()]), available_at: s([e().datetime({ offset: !0 }), a()]), parent_run_id: s([e(), a()]), created_at: e().datetime({ offset: !0 }), updated_at: e().datetime({ offset: !0 }) }).strict(), trigger_snapshot: s([l({ schema_version: g("research-snapshot-manifest.v1"), snapshot_id: e().min(1), run_id: e().min(1), event_id: e().min(1), snapshot_type: c(["trigger", "decision"]), generation: r().int().gte(1), parent_snapshot_id: s([e(), a()]), cutoff_at: e().datetime({ offset: !0 }), snapshot_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), pack_version: e().min(1), created_at: e().datetime({ offset: !0 }) }).strict(), a()]), decision_snapshot: s([l({ schema_version: g("research-snapshot-manifest.v1"), snapshot_id: e().min(1), run_id: e().min(1), event_id: e().min(1), snapshot_type: c(["trigger", "decision"]), generation: r().int().gte(1), parent_snapshot_id: s([e(), a()]), cutoff_at: e().datetime({ offset: !0 }), snapshot_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), pack_version: e().min(1), created_at: e().datetime({ offset: !0 }) }).strict(), a()]), evidence: o(l({ evidence_id: e().min(1), requirement_id: e().min(1), kind: c(["official", "market", "web", "transcript", "document"]), authority: c(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3), structured_payload_ref: s([e(), a()]), tool_call_id: s([e(), a()]), research_session_id: e().min(1), round: r().int().gte(1), quality: c(["candidate", "accepted", "rejected", "stale", "conflicted"]), freshness_status: c(["fresh", "stale", "unknown"]), conflict_group: s([e(), a()]) }).strict()), rounds: o(l({ round: r().int().gte(1), plan: l({ plan_id: e().min(1), objective: e().min(1), tasks: o(l({ task_id: e().min(1), capability_id: e().min(1), objective: e().min(1), question: e().min(1), input_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), output_schema_ref: e().min(1), depends_on: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), success_condition: e().min(1), priority: r().int().gte(1).lte(100) }).strict()).min(1), required_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), created_at: e().datetime({ offset: !0 }) }).strict(), tool_invocations: o(l({ tool_call_id: e().min(1), capability_id: e().min(1), tool_name: e().min(1), query_summary: e().min(1).max(1e3), started_at: e().datetime({ offset: !0 }), finished_at: s([e().datetime({ offset: !0 }), a()]), status: c(["running", "succeeded", "failed", "denied", "timed_out"]), attempt: r().int().gte(1), latency_ms: s([r().int().gte(0), a()]), cost_usd: s([r().gte(0), a()]), error_code: s([e(), a()]), error: s([l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict(), a()]).optional() }).strict()), tool_results: o(l({ tool_call_id: e().min(1), status: c(["succeeded", "failed", "denied", "timed_out"]), summary: e().min(1).max(2e3), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), content_ref: s([e(), a()]), content_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), received_at: e().datetime({ offset: !0 }), error_code: s([e(), a()]), error: s([l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict(), a()]).optional() }).strict()), new_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), coverage: l({ status: c(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(l({ requirement_id: e().min(1), importance: c(["hard", "soft"]), reason_code: c(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: b() }).strict()), conflicts: o(l({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: c(["hard", "soft"]), summary: e().min(1), resolution_status: c(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }) }).strict()), causal_case: s([l({ case_id: e().min(1), thesis: e().min(1), main_chain: o(l({ link_id: e().min(1), claim_type: c(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(c(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), opposite_chain: o(l({ link_id: e().min(1), claim_type: c(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(c(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), unresolved_questions: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), a()]), horizons: o(l({ horizon: c(["30m", "24h", "72h"]), action: c(["long", "short", "neutral", "no_trade"]), subjective_probability: r().gte(0).lte(1), probability_status: c(["uncalibrated", "calibrated"]), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), trigger: e().min(1), invalidation: e().min(1), expires_at: e().datetime({ offset: !0 }), next_review_at: e().datetime({ offset: !0 }), missing_facts: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap_reason: s([e(), a()]) }).strict()).max(3), trace: o(l({ schema_version: g("research-trace-event.v1"), run_id: e().min(1), research_session_id: e().min(1), sequence_no: r().int().gte(0), event_type: c(["session_started", "plan_created", "task_started", "model_step_started", "model_step_completed", "tool_started", "tool_completed", "tool_failed", "subagent_started", "subagent_completed", "evidence_accepted", "evidence_rejected", "coverage_assessed", "replan", "round_completed", "synthesis_started", "session_stopped"]), occurred_at: e().datetime({ offset: !0 }), stage: e().min(1), summary: e().min(1).max(2e3), reference_type: s([e(), a()]), reference_id: s([e(), a()]), status: c(["running", "succeeded", "failed", "degraded", "denied"]), error_code: s([e(), a()]), error: s([l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict(), a()]).optional() }).strict()), total_tool_calls: r().int().gte(0), total_subagents: r().int().gte(0), total_tokens: s([r().int().gte(0), a()]), estimated_cost_usd: s([r().gte(0), a()]), scheduled_recheck_at: s([e().datetime({ offset: !0 }), a()]) }).strict(), Nt = l({ schema_version: g("research-run-queued.v1"), event_id: e().min(1).max(128), run_id: e().min(1).max(128), status: g("queued"), status_url: e().min(1).max(2048) }).strict();
l({ schema_version: g("research-run-view.v2"), run_id: e().min(1), event_id: e().min(1), event_title: e().min(1), admission_origin: c(["manual", "automatic", "scheduled_recheck", "legacy"]), priority: c(["critical", "high", "normal", "low"]), status: c(["admitted", "queued", "researching", "retry_wait", "completed", "degraded", "research_only", "rejected", "failed", "cancelled"]), stage: c(["admission", "planning", "acquiring_evidence", "assessing_sufficiency", "synthesis", "gate", "monitoring", "done"]), runtime_id: e().min(1), profile_ref: e().min(1), current_round: r().int().gte(0), budget: l({ max_evidence_rounds: r().int().gte(1).lte(10), max_tool_calls: r().int().gte(1).lte(100), max_subagents: r().int().gte(0).lte(32), total_deadline_seconds: r().int().gte(1).lte(3600), per_tool_timeout_seconds: r().int().gte(1).lte(600), per_model_step_timeout_seconds: r().int().gte(1).lte(600), max_structured_repairs: r().int().gte(0).lte(5), max_estimated_cost_usd: r().gte(0) }).strict(), coverage: s([l({ status: c(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(l({ requirement_id: e().min(1), importance: c(["hard", "soft"]), reason_code: c(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: b() }).strict()), conflicts: o(l({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: c(["hard", "soft"]), summary: e().min(1), resolution_status: c(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), a()]), current_action: s([e(), a()]), stop_reason: s([l({ code: c(["sufficient", "deadline", "cost_budget", "tool_budget", "round_budget", "permission_denied", "critical_data_unavailable", "runtime_unavailable", "cancelled"]), detail: e().min(1), bounded: b(), remaining_hard_gaps: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), a()]), failure: s([l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict(), a()]).optional(), latest_sequence_no: r().int().gte(0), artifact_id: s([e(), a()]), available_at: s([e().datetime({ offset: !0 }), a()]), parent_run_id: s([e(), a()]), created_at: e().datetime({ offset: !0 }), updated_at: e().datetime({ offset: !0 }) }).strict();
l({ schema_version: g("research-runtime-case-report.v1"), dataset_id: e().min(1), case_id: e().min(1), runtime_id: e().min(1), runtime_version: e().min(1), status: c(["completed", "degraded", "failed", "timeout"]), cutoff_at: e().datetime({ offset: !0 }), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: s([r().int().gte(0), a()]), result_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), result: s([l({ schema_version: g("research-session-result.v1"), request_id: e().min(1), research_session_id: e().min(1), runtime_id: e().min(1), runtime_version: e().min(1), profile_ref: e().min(1), trace_ref: e().min(1), trace_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), status: c(["completed", "degraded", "failed", "cancelled"]), rounds: o(l({ round: r().int().gte(1), plan: l({ plan_id: e().min(1), objective: e().min(1), tasks: o(l({ task_id: e().min(1), capability_id: e().min(1), objective: e().min(1), question: e().min(1), input_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), output_schema_ref: e().min(1), depends_on: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), success_condition: e().min(1), priority: r().int().gte(1).lte(100) }).strict()).min(1), required_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), created_at: e().datetime({ offset: !0 }) }).strict(), tool_invocations: o(l({ tool_call_id: e().min(1), capability_id: e().min(1), tool_name: e().min(1), query_summary: e().min(1).max(1e3), started_at: e().datetime({ offset: !0 }), finished_at: s([e().datetime({ offset: !0 }), a()]), status: c(["running", "succeeded", "failed", "denied", "timed_out"]), attempt: r().int().gte(1), latency_ms: s([r().int().gte(0), a()]), cost_usd: s([r().gte(0), a()]), error_code: s([e(), a()]), error: s([l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict(), a()]).optional() }).strict()), tool_results: o(l({ tool_call_id: e().min(1), status: c(["succeeded", "failed", "denied", "timed_out"]), summary: e().min(1).max(2e3), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), content_ref: s([e(), a()]), content_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), received_at: e().datetime({ offset: !0 }), error_code: s([e(), a()]), error: s([l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict(), a()]).optional() }).strict()), new_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), coverage: l({ status: c(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(l({ requirement_id: e().min(1), importance: c(["hard", "soft"]), reason_code: c(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: b() }).strict()), conflicts: o(l({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: c(["hard", "soft"]), summary: e().min(1), resolution_status: c(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }) }).strict()).min(1), evidence_candidates: o(l({ evidence_id: e().min(1), requirement_id: e().min(1), kind: c(["official", "market", "web", "transcript", "document"]), authority: c(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3), structured_payload_ref: s([e(), a()]), tool_call_id: s([e(), a()]), research_session_id: e().min(1), round: r().int().gte(1), quality: c(["candidate", "accepted", "rejected", "stale", "conflicted"]), freshness_status: c(["fresh", "stale", "unknown"]), conflict_group: s([e(), a()]) }).strict()), final_coverage: l({ status: c(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(l({ requirement_id: e().min(1), importance: c(["hard", "soft"]), reason_code: c(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: b() }).strict()), conflicts: o(l({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: c(["hard", "soft"]), summary: e().min(1), resolution_status: c(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), causal_case: s([l({ case_id: e().min(1), thesis: e().min(1), main_chain: o(l({ link_id: e().min(1), claim_type: c(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(c(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), opposite_chain: o(l({ link_id: e().min(1), claim_type: c(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(c(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), unresolved_questions: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), a()]), horizons: o(l({ horizon: c(["30m", "24h", "72h"]), action: c(["long", "short", "neutral", "no_trade"]), subjective_probability: r().gte(0).lte(1), probability_status: c(["uncalibrated", "calibrated"]), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), trigger: e().min(1), invalidation: e().min(1), expires_at: e().datetime({ offset: !0 }), next_review_at: e().datetime({ offset: !0 }), missing_facts: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap_reason: s([e(), a()]) }).strict()).max(3), stop_reason: l({ code: c(["sufficient", "deadline", "cost_budget", "tool_budget", "round_budget", "permission_denied", "critical_data_unavailable", "runtime_unavailable", "cancelled"]), detail: e().min(1), bounded: b(), remaining_hard_gaps: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), synthesis_failure_code: s([e().max(128), a()]).optional(), total_tool_calls: r().int().gte(0), total_subagents: r().int().gte(0), total_tokens: s([r().int().gte(0), a()]), estimated_cost_usd: s([r().gte(0), a()]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }) }).strict(), a()]), hard_coverage_ratio: r().gte(0).lte(1), hard_gap_count: r().int().gte(0), evidence_count: r().int().gte(0), unattested_evidence_count: r().int().gte(0), pit_violations: r().int().gte(0), horizon_count: r().int().gte(0).lte(3), horizon_distinct: b(), tool_calls: r().int().gte(0), subagents: r().int().gte(0), total_tokens: s([r().int().gte(0), a()]), estimated_cost_usd: s([r().gte(0), a()]), failure_code: s([e(), a()]) }).strict();
l({ schema_version: g("research-runtime-comparison.v1"), experiment_id: e().min(1), dataset_id: e().min(1), dataset_manifest_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), baseline_runtime_id: e().min(1), candidate_runtime_id: e().min(1), case_reports: o(l({ schema_version: g("research-runtime-case-report.v1"), dataset_id: e().min(1), case_id: e().min(1), runtime_id: e().min(1), runtime_version: e().min(1), status: c(["completed", "degraded", "failed", "timeout"]), cutoff_at: e().datetime({ offset: !0 }), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: s([r().int().gte(0), a()]), result_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), result: s([l({ schema_version: g("research-session-result.v1"), request_id: e().min(1), research_session_id: e().min(1), runtime_id: e().min(1), runtime_version: e().min(1), profile_ref: e().min(1), trace_ref: e().min(1), trace_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), status: c(["completed", "degraded", "failed", "cancelled"]), rounds: o(l({ round: r().int().gte(1), plan: l({ plan_id: e().min(1), objective: e().min(1), tasks: o(l({ task_id: e().min(1), capability_id: e().min(1), objective: e().min(1), question: e().min(1), input_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), output_schema_ref: e().min(1), depends_on: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), success_condition: e().min(1), priority: r().int().gte(1).lte(100) }).strict()).min(1), required_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), created_at: e().datetime({ offset: !0 }) }).strict(), tool_invocations: o(l({ tool_call_id: e().min(1), capability_id: e().min(1), tool_name: e().min(1), query_summary: e().min(1).max(1e3), started_at: e().datetime({ offset: !0 }), finished_at: s([e().datetime({ offset: !0 }), a()]), status: c(["running", "succeeded", "failed", "denied", "timed_out"]), attempt: r().int().gte(1), latency_ms: s([r().int().gte(0), a()]), cost_usd: s([r().gte(0), a()]), error_code: s([e(), a()]), error: s([l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict(), a()]).optional() }).strict()), tool_results: o(l({ tool_call_id: e().min(1), status: c(["succeeded", "failed", "denied", "timed_out"]), summary: e().min(1).max(2e3), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), content_ref: s([e(), a()]), content_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), received_at: e().datetime({ offset: !0 }), error_code: s([e(), a()]), error: s([l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict(), a()]).optional() }).strict()), new_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), coverage: l({ status: c(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(l({ requirement_id: e().min(1), importance: c(["hard", "soft"]), reason_code: c(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: b() }).strict()), conflicts: o(l({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: c(["hard", "soft"]), summary: e().min(1), resolution_status: c(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }) }).strict()).min(1), evidence_candidates: o(l({ evidence_id: e().min(1), requirement_id: e().min(1), kind: c(["official", "market", "web", "transcript", "document"]), authority: c(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3), structured_payload_ref: s([e(), a()]), tool_call_id: s([e(), a()]), research_session_id: e().min(1), round: r().int().gte(1), quality: c(["candidate", "accepted", "rejected", "stale", "conflicted"]), freshness_status: c(["fresh", "stale", "unknown"]), conflict_group: s([e(), a()]) }).strict()), final_coverage: l({ status: c(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(l({ requirement_id: e().min(1), importance: c(["hard", "soft"]), reason_code: c(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: b() }).strict()), conflicts: o(l({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: c(["hard", "soft"]), summary: e().min(1), resolution_status: c(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), causal_case: s([l({ case_id: e().min(1), thesis: e().min(1), main_chain: o(l({ link_id: e().min(1), claim_type: c(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(c(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), opposite_chain: o(l({ link_id: e().min(1), claim_type: c(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(c(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), unresolved_questions: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), a()]), horizons: o(l({ horizon: c(["30m", "24h", "72h"]), action: c(["long", "short", "neutral", "no_trade"]), subjective_probability: r().gte(0).lte(1), probability_status: c(["uncalibrated", "calibrated"]), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), trigger: e().min(1), invalidation: e().min(1), expires_at: e().datetime({ offset: !0 }), next_review_at: e().datetime({ offset: !0 }), missing_facts: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap_reason: s([e(), a()]) }).strict()).max(3), stop_reason: l({ code: c(["sufficient", "deadline", "cost_budget", "tool_budget", "round_budget", "permission_denied", "critical_data_unavailable", "runtime_unavailable", "cancelled"]), detail: e().min(1), bounded: b(), remaining_hard_gaps: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), synthesis_failure_code: s([e().max(128), a()]).optional(), total_tool_calls: r().int().gte(0), total_subagents: r().int().gte(0), total_tokens: s([r().int().gte(0), a()]), estimated_cost_usd: s([r().gte(0), a()]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }) }).strict(), a()]), hard_coverage_ratio: r().gte(0).lte(1), hard_gap_count: r().int().gte(0), evidence_count: r().int().gte(0), unattested_evidence_count: r().int().gte(0), pit_violations: r().int().gte(0), horizon_count: r().int().gte(0).lte(3), horizon_distinct: b(), tool_calls: r().int().gte(0), subagents: r().int().gte(0), total_tokens: s([r().int().gte(0), a()]), estimated_cost_usd: s([r().gte(0), a()]), failure_code: s([e(), a()]) }).strict()).min(1), summaries: o(l({ runtime_id: e().min(1), runtime_version: e().min(1), sample_count: r().int().gte(0), completed_count: r().int().gte(0), failed_count: r().int().gte(0), hard_coverage_mean: r().gte(0).lte(1), evidence_count: r().int().gte(0), unattested_evidence_count: r().int().gte(0), pit_violations: r().int().gte(0), horizon_distinct_count: r().int().gte(0), total_tool_calls: r().int().gte(0), total_subagents: r().int().gte(0), total_tokens: s([r().int().gte(0), a()]), estimated_cost_usd: s([r().gte(0), a()]), p95_latency_ms: s([r().int().gte(0), a()]), failure_counts: E(r().int().gte(1)) }).strict()).min(2).max(2), conclusion: c(["pending", "promotion_recommended", "retain_baseline"]) }).strict();
l({ runtime_id: e().min(1), runtime_version: e().min(1), sample_count: r().int().gte(0), completed_count: r().int().gte(0), failed_count: r().int().gte(0), hard_coverage_mean: r().gte(0).lte(1), evidence_count: r().int().gte(0), unattested_evidence_count: r().int().gte(0), pit_violations: r().int().gte(0), horizon_distinct_count: r().int().gte(0), total_tool_calls: r().int().gte(0), total_subagents: r().int().gte(0), total_tokens: s([r().int().gte(0), a()]), estimated_cost_usd: s([r().gte(0), a()]), p95_latency_ms: s([r().int().gte(0), a()]), failure_counts: E(r().int().gte(1)) }).strict();
l({ schema_version: g("research-session-request.v1"), request_id: e().min(1), run_id: e().min(1), event_id: e().min(1), trigger_snapshot_id: e().min(1), domain_pack_ref: e().min(1), role_profile_ref: e().min(1), execution_mode: c(["live", "replay"]), pit_cutoff_at: e().datetime({ offset: !0 }), current_round: r().int().gte(1).lte(10), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), input_evidence: o(l({ evidence_id: e().min(1), kind: c(["official", "market", "web", "transcript", "document"]), authority: c(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3) }).strict()).min(1).max(50), evidence_requirements: o(l({ requirement_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), description: e().min(1), importance: c(["hard", "soft"]), source_priority: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), authority_floor: c(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), preferred_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), freshness_seconds: r().int().gte(0), minimum_independent_sources: r().int().gte(1).lte(10), allowed_fallbacks: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap: r().gte(0).lte(1) }).strict()).min(1), target_gaps: o(l({ requirement_id: e().min(1), importance: c(["hard", "soft"]), reason_code: c(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: b() }).strict()).max(50), allowed_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), execution_budget: l({ max_evidence_rounds: r().int().gte(1).lte(10), max_tool_calls: r().int().gte(1).lte(100), max_subagents: r().int().gte(0).lte(32), total_deadline_seconds: r().int().gte(1).lte(3600), per_tool_timeout_seconds: r().int().gte(1).lte(600), per_model_step_timeout_seconds: r().int().gte(1).lte(600), max_structured_repairs: r().int().gte(0).lte(5), max_estimated_cost_usd: r().gte(0) }).strict(), deadline_at: e().datetime({ offset: !0 }), output_schema_ref: e().min(1), repair_instructions: s([e().max(4e3), a()]) }).strict();
l({ schema_version: g("research-session-result.v1"), request_id: e().min(1), research_session_id: e().min(1), runtime_id: e().min(1), runtime_version: e().min(1), profile_ref: e().min(1), trace_ref: e().min(1), trace_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), status: c(["completed", "degraded", "failed", "cancelled"]), rounds: o(l({ round: r().int().gte(1), plan: l({ plan_id: e().min(1), objective: e().min(1), tasks: o(l({ task_id: e().min(1), capability_id: e().min(1), objective: e().min(1), question: e().min(1), input_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), output_schema_ref: e().min(1), depends_on: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), success_condition: e().min(1), priority: r().int().gte(1).lte(100) }).strict()).min(1), required_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), created_at: e().datetime({ offset: !0 }) }).strict(), tool_invocations: o(l({ tool_call_id: e().min(1), capability_id: e().min(1), tool_name: e().min(1), query_summary: e().min(1).max(1e3), started_at: e().datetime({ offset: !0 }), finished_at: s([e().datetime({ offset: !0 }), a()]), status: c(["running", "succeeded", "failed", "denied", "timed_out"]), attempt: r().int().gte(1), latency_ms: s([r().int().gte(0), a()]), cost_usd: s([r().gte(0), a()]), error_code: s([e(), a()]), error: s([l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict(), a()]).optional() }).strict()), tool_results: o(l({ tool_call_id: e().min(1), status: c(["succeeded", "failed", "denied", "timed_out"]), summary: e().min(1).max(2e3), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), content_ref: s([e(), a()]), content_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), received_at: e().datetime({ offset: !0 }), error_code: s([e(), a()]), error: s([l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict(), a()]).optional() }).strict()), new_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), coverage: l({ status: c(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(l({ requirement_id: e().min(1), importance: c(["hard", "soft"]), reason_code: c(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: b() }).strict()), conflicts: o(l({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: c(["hard", "soft"]), summary: e().min(1), resolution_status: c(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }) }).strict()).min(1), evidence_candidates: o(l({ evidence_id: e().min(1), requirement_id: e().min(1), kind: c(["official", "market", "web", "transcript", "document"]), authority: c(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3), structured_payload_ref: s([e(), a()]), tool_call_id: s([e(), a()]), research_session_id: e().min(1), round: r().int().gte(1), quality: c(["candidate", "accepted", "rejected", "stale", "conflicted"]), freshness_status: c(["fresh", "stale", "unknown"]), conflict_group: s([e(), a()]) }).strict()), final_coverage: l({ status: c(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(l({ requirement_id: e().min(1), importance: c(["hard", "soft"]), reason_code: c(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: b() }).strict()), conflicts: o(l({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: c(["hard", "soft"]), summary: e().min(1), resolution_status: c(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), causal_case: s([l({ case_id: e().min(1), thesis: e().min(1), main_chain: o(l({ link_id: e().min(1), claim_type: c(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(c(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), opposite_chain: o(l({ link_id: e().min(1), claim_type: c(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(c(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), unresolved_questions: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), a()]), horizons: o(l({ horizon: c(["30m", "24h", "72h"]), action: c(["long", "short", "neutral", "no_trade"]), subjective_probability: r().gte(0).lte(1), probability_status: c(["uncalibrated", "calibrated"]), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), trigger: e().min(1), invalidation: e().min(1), expires_at: e().datetime({ offset: !0 }), next_review_at: e().datetime({ offset: !0 }), missing_facts: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap_reason: s([e(), a()]) }).strict()).max(3), stop_reason: l({ code: c(["sufficient", "deadline", "cost_budget", "tool_budget", "round_budget", "permission_denied", "critical_data_unavailable", "runtime_unavailable", "cancelled"]), detail: e().min(1), bounded: b(), remaining_hard_gaps: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), synthesis_failure_code: s([e().max(128), a()]).optional(), total_tool_calls: r().int().gte(0), total_subagents: r().int().gte(0), total_tokens: s([r().int().gte(0), a()]), estimated_cost_usd: s([r().gte(0), a()]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }) }).strict();
l({ schema_version: g("research-snapshot-manifest.v1"), snapshot_id: e().min(1), run_id: e().min(1), event_id: e().min(1), snapshot_type: c(["trigger", "decision"]), generation: r().int().gte(1), parent_snapshot_id: s([e(), a()]), cutoff_at: e().datetime({ offset: !0 }), snapshot_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), pack_version: e().min(1), created_at: e().datetime({ offset: !0 }) }).strict();
l({ code: c(["sufficient", "deadline", "cost_budget", "tool_budget", "round_budget", "permission_denied", "critical_data_unavailable", "runtime_unavailable", "cancelled"]), detail: e().min(1), bounded: b(), remaining_hard_gaps: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict();
l({ schema_version: g("research-synthesis-candidate.v1"), request_id: e().min(1), causal_case: s([l({ case_id: e().min(1), thesis: e().min(1), main_chain: o(l({ link_id: e().min(1), claim_type: c(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(c(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), opposite_chain: o(l({ link_id: e().min(1), claim_type: c(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(c(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), unresolved_questions: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), a()]), horizons: o(l({ horizon: c(["30m", "24h", "72h"]), action: c(["long", "short", "neutral", "no_trade"]), subjective_probability: r().gte(0).lte(1), probability_status: c(["uncalibrated", "calibrated"]), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), trigger: e().min(1), invalidation: e().min(1), expires_at: e().datetime({ offset: !0 }), next_review_at: e().datetime({ offset: !0 }), missing_facts: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap_reason: s([e(), a()]) }).strict()).max(3) }).strict();
l({ task_id: e().min(1), capability_id: e().min(1), objective: e().min(1), question: e().min(1), input_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), output_schema_ref: e().min(1), depends_on: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), success_condition: e().min(1), priority: r().int().gte(1).lte(100) }).strict();
l({ schema_version: g("research-trace-event.v1"), run_id: e().min(1), research_session_id: e().min(1), sequence_no: r().int().gte(0), event_type: c(["session_started", "plan_created", "task_started", "model_step_started", "model_step_completed", "tool_started", "tool_completed", "tool_failed", "subagent_started", "subagent_completed", "evidence_accepted", "evidence_rejected", "coverage_assessed", "replan", "round_completed", "synthesis_started", "session_stopped"]), occurred_at: e().datetime({ offset: !0 }), stage: e().min(1), summary: e().min(1).max(2e3), reference_type: s([e(), a()]), reference_id: s([e(), a()]), status: c(["running", "succeeded", "failed", "degraded", "denied"]), error_code: s([e(), a()]), error: s([l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict(), a()]).optional() }).strict();
l({ schema_version: g("role-profile.v1"), profile_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), version: e().min(1), objective: e().min(1), required_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), allowed_tools: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), output_schema_ref: e().min(1), evaluation_policy_ref: e().min(1), system_instruction_ref: e().min(1) }).strict();
l({ tool_call_id: e().min(1), capability_id: e().min(1), tool_name: e().min(1), query_summary: e().min(1).max(1e3), started_at: e().datetime({ offset: !0 }), finished_at: s([e().datetime({ offset: !0 }), a()]), status: c(["running", "succeeded", "failed", "denied", "timed_out"]), attempt: r().int().gte(1), latency_ms: s([r().int().gte(0), a()]), cost_usd: s([r().gte(0), a()]), error_code: s([e(), a()]), error: s([l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict(), a()]).optional() }).strict();
l({ tool_call_id: e().min(1), status: c(["succeeded", "failed", "denied", "timed_out"]), summary: e().min(1).max(2e3), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), content_ref: s([e(), a()]), content_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), received_at: e().datetime({ offset: !0 }), error_code: s([e(), a()]), error: s([l({ error_code: e().min(1), origin: c(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: b(), deadline_ms: s([r().int().gte(0), a()]) }).strict(), a()]).optional() }).strict();
l({ proposal_id: e().min(1), candidate_type: c(["strategy", "runtime", "profile", "doctrine", "provider_policy"]), version: e().min(1), parent_version: s([e(), a()]), summary: e().min(1).max(1e4), changes: o(e().min(1).max(5e3)).min(1), rationale: e().min(1).max(1e4), evidence_refs: o(e().min(1)) }).strict();
l({ capability_id: e().min(1), capability_type: e().min(1), status: e().min(1), permissions: o(e()), network_domains: o(e()), timeout_seconds: r().gt(0), max_cost_usd: s([r().gte(0), a()]) }).strict();
l({ job_id: e().min(1), schema_version: g("evolution-job.v1"), trigger_key: e().min(1).max(512), trigger_type: c(["scheduled", "feedback", "failure_pattern", "evaluation_batch"]), domain_pack_ref: e().min(1), status: c(["queued", "running", "retry_wait", "pending_owner_review", "completed", "failed", "cancelled"]), stage: c(["discover", "plan", "candidate", "replay", "holdout", "shadow", "review"]), input_refs: o(e().min(1)).min(1), candidate_id: s([e(), a()]), experiment_refs: o(e().min(1)), result_refs: o(e().min(1)), attempt: r().int().gte(0), max_attempts: r().int().gte(1).lte(10), lease_owner: s([e(), a()]), lease_expires_at: s([e().datetime({ offset: !0 }), a()]), next_attempt_at: s([e().datetime({ offset: !0 }), a()]), last_error_code: s([e(), a()]), created_at: e().datetime({ offset: !0 }), updated_at: e().datetime({ offset: !0 }), finished_at: s([e().datetime({ offset: !0 }), a()]) }).strict();
l({ queued: r().int().gte(0), running: r().int().gte(0), retry_wait: r().int().gte(0), pending_owner_review: r().int().gte(0), completed: r().int().gte(0), failed: r().int().gte(0), cancelled: r().int().gte(0) }).strict();
l({ trigger_key: e().min(1).max(512), trigger_type: c(["scheduled", "feedback", "failure_pattern", "evaluation_batch"]), domain_pack_ref: e().min(1), input_refs: o(e().min(1)).min(1), max_attempts: r().int().gte(1).lte(10) }).strict();
l({ job_id: e().min(1), domain_pack_ref: e().min(1), context_items: o(e().min(1)).min(1), evidence_refs: o(e().min(1)), active_candidate_id: s([e(), a()]), active_candidate_version: s([e(), a()]), active_candidate_content_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]) }).strict();
l({ schema_version: g("operations-overview.v1"), checked_at: e().datetime({ offset: !0 }), services: o(l({ service_id: e().min(1), role: c(["api", "realtime_worker", "research_worker", "evolution_worker"]), instance_id: s([e().min(1), a()]), version: s([e().min(1), a()]), mode: s([e().min(1), a()]), status: c(["online", "stale", "offline"]), interval_seconds: s([r().gt(0).lte(3600), a()]), started_at: s([e().datetime({ offset: !0 }), a()]), heartbeat_at: s([e().datetime({ offset: !0 }), a()]), last_error_code: s([e(), a()]) }).strict()), runtime: l({ runtime_id: e().min(1), runtime_version: e().min(1), mode: c(["fake", "replay", "provider"]), provider_configured: b(), live_canary_status: c(["not_run", "passed", "failed", "unknown"]), model: s([e(), a()]), api_mode: s([e(), a()]) }).strict(), sources: o(l({ source_id: e().min(1), status: e().min(1), enabled: b(), cursor: s([e(), a()]), last_success_at: s([e().datetime({ offset: !0 }), a()]), next_poll_at: s([e().datetime({ offset: !0 }), a()]), consecutive_failures: r().int().gte(0), error_code: s([e(), a()]) }).strict()), capabilities: o(l({ capability_id: e().min(1), capability_type: e().min(1), status: e().min(1), permissions: o(e()), network_domains: o(e()), timeout_seconds: r().gt(0), max_cost_usd: s([r().gte(0), a()]) }).strict()), jobs: l({ queued: r().int().gte(0), running: r().int().gte(0), retry_wait: r().int().gte(0), pending_owner_review: r().int().gte(0), completed: r().int().gte(0), failed: r().int().gte(0), cancelled: r().int().gte(0) }).strict(), recent_jobs: o(l({ job_id: e().min(1), schema_version: g("evolution-job.v1"), trigger_key: e().min(1).max(512), trigger_type: c(["scheduled", "feedback", "failure_pattern", "evaluation_batch"]), domain_pack_ref: e().min(1), status: c(["queued", "running", "retry_wait", "pending_owner_review", "completed", "failed", "cancelled"]), stage: c(["discover", "plan", "candidate", "replay", "holdout", "shadow", "review"]), input_refs: o(e().min(1)).min(1), candidate_id: s([e(), a()]), experiment_refs: o(e().min(1)), result_refs: o(e().min(1)), attempt: r().int().gte(0), max_attempts: r().int().gte(1).lte(10), lease_owner: s([e(), a()]), lease_expires_at: s([e().datetime({ offset: !0 }), a()]), next_attempt_at: s([e().datetime({ offset: !0 }), a()]), last_error_code: s([e(), a()]), created_at: e().datetime({ offset: !0 }), updated_at: e().datetime({ offset: !0 }), finished_at: s([e().datetime({ offset: !0 }), a()]) }).strict()) }).strict();
l({ runtime_id: e().min(1), runtime_version: e().min(1), mode: c(["fake", "replay", "provider"]), provider_configured: b(), live_canary_status: c(["not_run", "passed", "failed", "unknown"]), model: s([e(), a()]), api_mode: s([e(), a()]) }).strict();
l({ evidence_id: e().min(1), title: e().min(1), snippet: e().min(1).max(2e4), source_url: e().url(), observed_at: e().datetime({ offset: !0 }), published_at: s([e().datetime({ offset: !0 }), a()]), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")) }).strict();
l({ request_id: e().min(1), capability_id: e().min(1), query: e().min(1).max(2e3), allowed_domains: o(e().min(1)), max_results: r().int().gte(1).lte(20), max_cost_usd: s([r().gte(0), a()]), observed_at: e().datetime({ offset: !0 }) }).strict();
l({ request_id: e().min(1), capability_id: e().min(1), provider: e().min(1), evidence: o(l({ evidence_id: e().min(1), title: e().min(1), snippet: e().min(1).max(2e4), source_url: e().url(), observed_at: e().datetime({ offset: !0 }), published_at: s([e().datetime({ offset: !0 }), a()]), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")) }).strict()), cost_usd: s([r().gte(0), a()]), completed_at: e().datetime({ offset: !0 }) }).strict();
l({ service_id: e().min(1), role: c(["api", "realtime_worker", "research_worker", "evolution_worker"]), instance_id: s([e().min(1), a()]), version: s([e().min(1), a()]), mode: s([e().min(1), a()]), status: c(["online", "stale", "offline"]), interval_seconds: s([r().gt(0).lte(3600), a()]), started_at: s([e().datetime({ offset: !0 }), a()]), heartbeat_at: s([e().datetime({ offset: !0 }), a()]), last_error_code: s([e(), a()]) }).strict();
l({ source_id: e().min(1), status: e().min(1), enabled: b(), cursor: s([e(), a()]), last_success_at: s([e().datetime({ offset: !0 }), a()]), next_poll_at: s([e().datetime({ offset: !0 }), a()]), consecutive_failures: r().int().gte(0), error_code: s([e(), a()]) }).strict();
l({ pointer_id: e().min(1), domain_pack_ref: e().min(1), candidate_id: e().min(1), generation: r().int().gte(0), updated_at: e().datetime({ offset: !0 }) }).strict();
l({ candidate_id: e().min(1), candidate_type: c(["strategy", "runtime", "profile", "doctrine", "provider_policy"]), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), version: e().min(1), parent_version: s([e(), a()]), status: c(["candidate", "experimental", "eligible", "active", "rejected", "retired"]), created_at: e().datetime({ offset: !0 }), content_ref: s([e(), a()]), source: e().min(1) }).strict();
l({ capability_id: e().min(1), version: e().min(1), capability_type: c(["source", "tool", "provider", "runtime", "strategy", "workbench"]), provider: e().min(1), license: e().min(1), input_schema_ref: e().min(1), output_schema_ref: e().min(1), permissions: o(e()), network_domains: o(e()), timeout_seconds: r().gt(0).lte(300), max_cost_usd: s([r().gte(0), a()]), status: c(["discovered", "audited", "enabled", "shadow", "rejected", "retired"]) }).strict();
l({ dataset_id: e().min(1), split: c(["replay", "holdout", "shadow", "live_observation"]), manifest_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), fixture_refs: o(e().min(1)).min(1), fixture_hashes: E(e().regex(new RegExp("^[a-f0-9]{64}$"))), cutoff_rule: g("published_at <= observed_at <= received_at"), label_rule: g("outcomes.available_at > received_at"), leakage_audit: c(["passed", "failed"]), authorization: e().min(1), visibility: g("owner_only"), source_mode: c(["fixture", "prospective"]), window_start_at: e().datetime({ offset: !0 }), window_end_at: e().datetime({ offset: !0 }), event_family_counts: E(r().int().gte(1)), created_at: e().datetime({ offset: !0 }) }).strict();
l({ datasets: o(l({ dataset_id: e().min(1), split: c(["replay", "holdout", "shadow", "live_observation"]), manifest_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), fixture_refs: o(e().min(1)).min(1), fixture_hashes: E(e().regex(new RegExp("^[a-f0-9]{64}$"))), cutoff_rule: g("published_at <= observed_at <= received_at"), label_rule: g("outcomes.available_at > received_at"), leakage_audit: c(["passed", "failed"]), authorization: e().min(1), visibility: g("owner_only"), source_mode: c(["fixture", "prospective"]), window_start_at: e().datetime({ offset: !0 }), window_end_at: e().datetime({ offset: !0 }), event_family_counts: E(r().int().gte(1)), created_at: e().datetime({ offset: !0 }) }).strict()), candidates: o(l({ candidate_id: e().min(1), candidate_type: c(["strategy", "runtime", "profile", "doctrine", "provider_policy"]), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), version: e().min(1), parent_version: s([e(), a()]), status: c(["candidate", "experimental", "eligible", "active", "rejected", "retired"]), created_at: e().datetime({ offset: !0 }), content_ref: s([e(), a()]), source: e().min(1) }).strict()), experiments: o(l({ experiment_id: e().min(1), dataset_id: e().min(1), baseline_ref: e().min(1), candidate_refs: o(e().min(1)).min(1), status: c(["registered", "running", "completed", "failed"]), created_at: e().datetime({ offset: !0 }), strategy_version: e().min(1), runtime_id: e().min(1), runtime_version: e().min(1), provider_id: s([e(), a()]), model: s([e(), a()]), schema_version: e().min(1), random_seed: s([r().int().gte(0), a()]), randomness_policy: c(["deterministic", "seeded", "provider_default"]), deadline_seconds: r().int().gte(1).lte(3600), max_cost_usd: s([r().gte(0), a()]) }).strict()), results: o(l({ result_id: e().min(1), experiment_id: e().min(1), candidate_id: e().min(1), sample_count: r().int().gte(0), brier_score: s([r().gte(0).lte(1), a()]), cost_usd: s([r().gte(0), a()]), p95_latency_ms: s([r().int().gte(0), a()]), safety_violations: r().int().gte(0), event_family_counts: E(r().int().gte(0)), created_at: e().datetime({ offset: !0 }), stage: c(["replay", "holdout", "shadow"]), failure_counts: E(r().int().gte(0)), evidence_coverage: s([r().gte(0).lte(1), a()]), directional_accuracy: s([r().gte(0).lte(1), a()]), raw_artifact_refs: o(e().min(1)).min(1), scorer_version: e().min(1) }).strict()), pointers: o(l({ pointer_id: e().min(1), domain_pack_ref: e().min(1), candidate_id: e().min(1), generation: r().int().gte(0), updated_at: e().datetime({ offset: !0 }) }).strict()), failures: o(l({ pattern_id: e().min(1), failure_code: e().min(1), occurrence_count: r().int().gte(1), impact: e().min(1), source_refs: o(e().min(1)).min(1), root_cause_hypothesis: s([e(), a()]), remediation_refs: o(e().min(1)), status: c(["open", "mitigated", "regressed", "closed"]), updated_at: e().datetime({ offset: !0 }) }).strict()), experiences: o(l({ request_id: e().min(1), domain_pack_ref: e().min(1), event_family: e().min(1), lesson: e().min(1).max(1e4), applicable_conditions: o(e()), evidence_refs: o(e().min(1)).min(1), outcome_refs: o(e().min(1)).min(1), evaluation_refs: o(e().min(1)).min(1), source_type: c(["owner", "evaluation", "failure_pattern", "agent"]), created_by: e().min(1), experience_id: e().min(1), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), status: c(["candidate", "verified", "retired"]), available_at: e().datetime({ offset: !0 }), created_at: e().datetime({ offset: !0 }) }).strict()), decisions: o(l({ decision_id: e().min(1), request_id: e().min(1), domain_pack_ref: e().min(1), candidate_id: e().min(1), owner: e().min(1), decision: c(["promote", "reject", "rollback"]), reason: e().min(1), evaluation_refs: o(e().min(1)), previous_candidate_id: s([e(), a()]), resulting_candidate_id: s([e(), a()]), resulting_generation: r().int().gte(0), created_at: e().datetime({ offset: !0 }) }).strict()) }).strict();
l({ request_id: e().min(1), domain_pack_ref: e().min(1), event_family: e().min(1), lesson: e().min(1).max(1e4), applicable_conditions: o(e()), evidence_refs: o(e().min(1)).min(1), outcome_refs: o(e().min(1)).min(1), evaluation_refs: o(e().min(1)).min(1), source_type: c(["owner", "evaluation", "failure_pattern", "agent"]), created_by: e().min(1), experience_id: e().min(1), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), status: c(["candidate", "verified", "retired"]), available_at: e().datetime({ offset: !0 }), created_at: e().datetime({ offset: !0 }) }).strict();
l({ request_id: e().min(1), domain_pack_ref: e().min(1), event_family: e().min(1), lesson: e().min(1).max(1e4), applicable_conditions: o(e()), evidence_refs: o(e().min(1)).min(1), outcome_refs: o(e().min(1)).min(1), evaluation_refs: o(e().min(1)).min(1), source_type: c(["owner", "evaluation", "failure_pattern", "agent"]), created_by: e().min(1) }).strict();
l({ experiment_id: e().min(1), dataset_id: e().min(1), baseline_ref: e().min(1), candidate_refs: o(e().min(1)).min(1), status: c(["registered", "running", "completed", "failed"]), created_at: e().datetime({ offset: !0 }), strategy_version: e().min(1), runtime_id: e().min(1), runtime_version: e().min(1), provider_id: s([e(), a()]), model: s([e(), a()]), schema_version: e().min(1), random_seed: s([r().int().gte(0), a()]), randomness_policy: c(["deterministic", "seeded", "provider_default"]), deadline_seconds: r().int().gte(1).lte(3600), max_cost_usd: s([r().gte(0), a()]) }).strict();
l({ result_id: e().min(1), experiment_id: e().min(1), candidate_id: e().min(1), sample_count: r().int().gte(0), brier_score: s([r().gte(0).lte(1), a()]), cost_usd: s([r().gte(0), a()]), p95_latency_ms: s([r().int().gte(0), a()]), safety_violations: r().int().gte(0), event_family_counts: E(r().int().gte(0)), created_at: e().datetime({ offset: !0 }), stage: c(["replay", "holdout", "shadow"]), failure_counts: E(r().int().gte(0)), evidence_coverage: s([r().gte(0).lte(1), a()]), directional_accuracy: s([r().gte(0).lte(1), a()]), raw_artifact_refs: o(e().min(1)).min(1), scorer_version: e().min(1) }).strict();
l({ pattern_id: e().min(1), failure_code: e().min(1), occurrence_count: r().int().gte(1), impact: e().min(1), source_refs: o(e().min(1)).min(1), root_cause_hypothesis: s([e(), a()]), remediation_refs: o(e().min(1)), status: c(["open", "mitigated", "regressed", "closed"]), updated_at: e().datetime({ offset: !0 }) }).strict();
l({ feedback_id: e().min(1), request_id: e().min(1), target_type: c(["run", "memo", "experiment", "candidate", "experience", "failure_pattern"]), target_id: e().min(1), created_by: e().min(1), verdict: c(["useful", "not_useful", "incorrect", "needs_review"]), notes: e().min(1).max(1e4), created_at: e().datetime({ offset: !0 }) }).strict();
l({ request_id: e().min(1), target_type: c(["run", "memo", "experiment", "candidate", "experience", "failure_pattern"]), target_id: e().min(1), created_by: e().min(1), verdict: c(["useful", "not_useful", "incorrect", "needs_review"]), notes: e().min(1).max(1e4) }).strict();
l({ decision_id: e().min(1), request_id: e().min(1), domain_pack_ref: e().min(1), candidate_id: e().min(1), owner: e().min(1), decision: c(["promote", "reject", "rollback"]), reason: e().min(1), evaluation_refs: o(e().min(1)), previous_candidate_id: s([e(), a()]), resulting_candidate_id: s([e(), a()]), resulting_generation: r().int().gte(0), created_at: e().datetime({ offset: !0 }) }).strict();
l({ request_id: e().min(1), candidate_id: e().min(1), owner: e().min(1), decision: c(["promote", "reject", "rollback"]), reason: e().min(1), evaluation_refs: o(e().min(1)), expected_generation: r().int().gte(0) }).strict();
l({ decision: l({ decision_id: e().min(1), request_id: e().min(1), domain_pack_ref: e().min(1), candidate_id: e().min(1), owner: e().min(1), decision: c(["promote", "reject", "rollback"]), reason: e().min(1), evaluation_refs: o(e().min(1)), previous_candidate_id: s([e(), a()]), resulting_candidate_id: s([e(), a()]), resulting_generation: r().int().gte(0), created_at: e().datetime({ offset: !0 }) }).strict(), active_pointer: s([l({ pointer_id: e().min(1), domain_pack_ref: e().min(1), candidate_id: e().min(1), generation: r().int().gte(0), updated_at: e().datetime({ offset: !0 }) }).strict(), a()]) }).strict();
l({ rule_id: e().min(1), status: c(["pass", "fail"]), reason_code: e().min(1), detail: e().min(1) }).strict();
l({ stage: c(["replay", "holdout", "shadow"]), result_id: e().min(1), baseline_result_id: e().min(1), sample_count: r().int().gte(0), candidate_brier_score: s([r().gte(0).lte(1), a()]), baseline_brier_score: s([r().gte(0).lte(1), a()]), brier_delta: s([r(), a()]), candidate_cost_usd: s([r().gte(0), a()]), baseline_cost_usd: s([r().gte(0), a()]), cost_ratio: s([r().gte(0), a()]), p95_latency_ms: s([r().int().gte(0), a()]), safety_violations: r().int().gte(0) }).strict();
l({ domain_pack_ref: e().min(1), candidate_id: e().min(1), evaluation_refs: o(e().min(1)), eligible: b(), checks: o(l({ rule_id: e().min(1), status: c(["pass", "fail"]), reason_code: e().min(1), detail: e().min(1) }).strict()).min(1), deltas: o(l({ stage: c(["replay", "holdout", "shadow"]), result_id: e().min(1), baseline_result_id: e().min(1), sample_count: r().int().gte(0), candidate_brier_score: s([r().gte(0).lte(1), a()]), baseline_brier_score: s([r().gte(0).lte(1), a()]), brier_delta: s([r(), a()]), candidate_cost_usd: s([r().gte(0), a()]), baseline_cost_usd: s([r().gte(0), a()]), cost_ratio: s([r().gte(0), a()]), p95_latency_ms: s([r().int().gte(0), a()]), safety_violations: r().int().gte(0) }).strict()) }).strict();
l({ candidate_id: e().min(1), evaluation_refs: o(e().min(1)) }).strict();
l({ memo_id: e().min(1), request_id: e().min(1), run_id: s([e(), a()]), snapshot_id: s([e(), a()]), domain_pack_ref: e().min(1), created_by: e().min(1), claims: o(e().min(1)).min(1), evidence_refs: o(e().min(1)).min(1), counterpoints: o(e().min(1)), uncertainties: o(e().min(1)), follow_up_questions: o(e().min(1)), status: c(["draft", "submitted", "reviewed", "accepted", "rejected", "superseded"]), created_at: e().datetime({ offset: !0 }) }).strict();
l({ request_id: e().min(1), run_id: s([e(), a()]), snapshot_id: s([e(), a()]), domain_pack_ref: e().min(1), created_by: e().min(1), claims: o(e().min(1)).min(1), evidence_refs: o(e().min(1)).min(1), counterpoints: o(e().min(1)), uncertainties: o(e().min(1)), follow_up_questions: o(e().min(1)) }).strict();
l({ memos: o(l({ memo_id: e().min(1), request_id: e().min(1), run_id: s([e(), a()]), snapshot_id: s([e(), a()]), domain_pack_ref: e().min(1), created_by: e().min(1), claims: o(e().min(1)).min(1), evidence_refs: o(e().min(1)).min(1), counterpoints: o(e().min(1)), uncertainties: o(e().min(1)), follow_up_questions: o(e().min(1)), status: c(["draft", "submitted", "reviewed", "accepted", "rejected", "superseded"]), created_at: e().datetime({ offset: !0 }) }).strict()), feedback: o(l({ feedback_id: e().min(1), request_id: e().min(1), target_type: c(["run", "memo", "experiment", "candidate", "experience", "failure_pattern"]), target_id: e().min(1), created_by: e().min(1), verdict: c(["useful", "not_useful", "incorrect", "needs_review"]), notes: e().min(1).max(1e4), created_at: e().datetime({ offset: !0 }) }).strict()), capabilities: o(l({ capability_id: e().min(1), version: e().min(1), capability_type: c(["source", "tool", "provider", "runtime", "strategy", "workbench"]), provider: e().min(1), license: e().min(1), input_schema_ref: e().min(1), output_schema_ref: e().min(1), permissions: o(e()), network_domains: o(e()), timeout_seconds: r().gt(0).lte(300), max_cost_usd: s([r().gte(0), a()]), status: c(["discovered", "audited", "enabled", "shadow", "rejected", "retired"]) }).strict()) }).strict();
const st = l({ evidence_id: e().min(1), source_id: e().min(1), source_type: e().min(1), observed_at: e().datetime({ offset: !0 }), published_at: s([e().datetime({ offset: !0 }), a()]), received_at: e().datetime({ offset: !0 }), cutoff_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")) }).strict(), at = l({ mode: c(["fixed_graph", "supervisor_candidate"]), supervisor_role: s([e(), a()]), planned_capabilities: o(e()), required_capabilities: o(e()), specialist_coverage: o(e()), missing_capabilities: o(e()), replan_count: r().int().gte(0).lte(1), experiment_refs: o(e()) }).strict(), rt = l({ sequence_no: r().int().gte(0), event_type: e().min(1), occurred_at: e().datetime({ offset: !0 }), reference_type: s([e(), a()]), reference_id: s([e(), a()]) }).strict(), ot = l({ strategy_version: e().min(1), runtime_version: e().min(1), pack_version: s([e(), a()]), provider_ids: o(e()), models: o(e()), schema_versions: o(e()), pricing_versions: o(e()) }).strict(), dt = c(["admitted", "running", "completed", "degraded", "failed", "cancelled"]), je = c(["publish", "degraded", "research_only", "reject"]), ct = c(["long", "short", "neutral", "no_trade"]);
l({
  source_id: e().min(1),
  source_type: e().min(1),
  version: e().min(1),
  capabilities: o(e()),
  authority_level: e(),
  poll_interval_seconds: r().positive(),
  max_batch: r().int().positive(),
  allowed_domains: o(e()).default([]),
  enabled: b()
});
const ut = l({
  source_id: e(),
  status: e(),
  cursor: e().nullable(),
  last_success_at: e().nullable(),
  last_error_at: e().nullable(),
  consecutive_failures: r().int().nonnegative(),
  latency_ms: r().nullable(),
  error_code: e().nullable(),
  next_poll_at: e().nullable()
});
l({
  instrument: e(),
  observed_at: e(),
  received_at: e(),
  bid: r().nullable(),
  ask: r().nullable(),
  last: r().nullable(),
  volume: r().nullable(),
  source_id: e(),
  quality_status: c(["observed", "estimated", "unavailable"]),
  benchmark: c(["first_executable", "vwap_1m", "last", "none"])
});
l({
  artifact_id: e(),
  channel: e(),
  dedupe_key: e(),
  subject: e(),
  body: e(),
  created_at: e()
});
l({
  status: c(["ok", "degraded"]),
  running_runs: r().int().nonnegative(),
  failed_runs: r().int().nonnegative(),
  sources: o(ut)
});
const lt = l({
  check_id: e().min(1),
  status: c(["pass", "fail", "warning"]),
  detail: e().min(1),
  error_code: e().nullable()
});
l({
  schema_version: g("pilot-readiness.v1"),
  status: c(["ready", "not_ready"]),
  checked_at: e(),
  pilot_mode: b(),
  notification_channel: c(["local", "email"]),
  source_ids: o(e()),
  checks: o(lt),
  live_canaries_required: o(e())
});
const mt = l({
  forecast_id: e(),
  artifact_id: e(),
  instrument: e(),
  horizon: e(),
  direction: ct,
  probability: r().min(0).max(1),
  trigger: e(),
  invalidation: e(),
  expires_at: e()
}), Te = l({
  run_id: e(),
  event_id: e(),
  status: dt,
  strategy_version: e(),
  runtime_version: e().default("unknown"),
  snapshot_id: e().nullable(),
  artifact_id: e().nullable(),
  created_at: e(),
  updated_at: e(),
  finished_at: e().nullable(),
  latency_ms: r().nullable(),
  cost_usd: r().nullable(),
  error_code: e().nullable(),
  headline: e().nullable(),
  gate_status: je.nullable()
}), _t = l({
  artifact_id: e(),
  run_id: e(),
  event_id: e(),
  gate_status: je,
  headline: e(),
  summary: e(),
  facts: o(e()),
  inferences: o(e()),
  counter_thesis: e(),
  uncertainty: o(e()),
  transmission_chain: o(e()),
  citations: o(e()),
  forecasts: o(mt),
  gate_decisions: o(l({ rule_id: e(), status: e(), reason_code: e(), input_hash: e() })),
  created_at: e()
});
l({
  inbox: l({ pending_count: r(), running_count: r(), latest: o(Te) }),
  published_count_30d: r(),
  forecast_count: r(),
  evaluated_count: r(),
  health_status: e(),
  active_strategy: e(),
  active_pack: e()
});
const ft = l({
  call_id: e(),
  run_id: e(),
  role: e(),
  status: e(),
  attempt: r(),
  started_at: e(),
  finished_at: e().nullable(),
  latency_ms: r().nullable(),
  runtime_id: e().nullable(),
  runtime_version: e().nullable(),
  provider_id: e().nullable(),
  model: e().nullable(),
  api_mode: e().nullable(),
  schema_version: e().nullable(),
  prompt_tokens: r().nullable(),
  completion_tokens: r().nullable(),
  total_tokens: r().nullable(),
  cost_usd: r().nullable(),
  cost_status: e(),
  pricing_version: e().nullable(),
  error_code: e().nullable(),
  retryable: b()
}), pt = l({
  step_id: e(),
  run_id: e(),
  step_name: e(),
  status: e(),
  attempt: r(),
  started_at: e(),
  finished_at: e().nullable(),
  latency_ms: r().nullable(),
  error_code: e().nullable()
});
l({
  run: Te,
  timeline: o(rt),
  steps: o(pt),
  calls: o(ft),
  artifact: _t.nullable(),
  evaluation_count: r(),
  evaluations: o(l({
    evaluation_id: e(),
    forecast_id: e(),
    brier_score: r(),
    net_return_pct: r(),
    direction_correct: b(),
    label_status: e(),
    evaluated_at: e()
  })),
  snapshot_cutoff_at: e().nullable(),
  snapshot_hash: e().nullable(),
  evidence_lineage: o(st),
  versions: ot,
  orchestration: at
}).strict();
export {
  z as Z,
  xt as a,
  gt as b,
  Nt as c,
  qt as d,
  Ct as e,
  wt as f,
  Ot as g,
  At as h,
  vt as i,
  ht as j,
  yt as k,
  bt as l,
  Et as m,
  Rt as n,
  kt as o,
  jt as p,
  St as q,
  $t as r,
  Tt as s
};
//# sourceMappingURL=index-DHMPbtxF.js.map
