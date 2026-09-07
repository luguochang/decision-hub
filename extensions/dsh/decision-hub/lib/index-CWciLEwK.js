var R;
(function(i) {
  i.assertEqual = (l) => {
  };
  function t(l) {
  }
  i.assertIs = t;
  function n(l) {
    throw new Error();
  }
  i.assertNever = n, i.arrayToEnum = (l) => {
    const m = {};
    for (const _ of l)
      m[_] = _;
    return m;
  }, i.getValidEnumValues = (l) => {
    const m = i.objectKeys(l).filter((f) => typeof l[l[f]] != "number"), _ = {};
    for (const f of m)
      _[f] = l[f];
    return i.objectValues(_);
  }, i.objectValues = (l) => i.objectKeys(l).map(function(m) {
    return l[m];
  }), i.objectKeys = typeof Object.keys == "function" ? (l) => Object.keys(l) : (l) => {
    const m = [];
    for (const _ in l)
      Object.prototype.hasOwnProperty.call(l, _) && m.push(_);
    return m;
  }, i.find = (l, m) => {
    for (const _ of l)
      if (m(_))
        return _;
  }, i.isInteger = typeof Number.isInteger == "function" ? (l) => Number.isInteger(l) : (l) => typeof l == "number" && Number.isFinite(l) && Math.floor(l) === l;
  function c(l, m = " | ") {
    return l.map((_) => typeof _ == "string" ? `'${_}'` : _).join(m);
  }
  i.joinValues = c, i.jsonStringifyReplacer = (l, m) => typeof m == "bigint" ? m.toString() : m;
})(R || (R = {}));
var _e;
(function(i) {
  i.mergeShapes = (t, n) => ({
    ...t,
    ...n
    // second overwrites first
  });
})(_e || (_e = {}));
const y = R.arrayToEnum([
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
      return y.undefined;
    case "string":
      return y.string;
    case "number":
      return Number.isNaN(i) ? y.nan : y.number;
    case "boolean":
      return y.boolean;
    case "function":
      return y.function;
    case "bigint":
      return y.bigint;
    case "symbol":
      return y.symbol;
    case "object":
      return Array.isArray(i) ? y.array : i === null ? y.null : i.then && typeof i.then == "function" && i.catch && typeof i.catch == "function" ? y.promise : typeof Map < "u" && i instanceof Map ? y.map : typeof Set < "u" && i instanceof Set ? y.set : typeof Date < "u" && i instanceof Date ? y.date : y.object;
    default:
      return y.unknown;
  }
}, v = R.arrayToEnum([
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
class Z extends Error {
  get errors() {
    return this.issues;
  }
  constructor(t) {
    super(), this.issues = [], this.addIssue = (c) => {
      this.issues = [...this.issues, c];
    }, this.addIssues = (c = []) => {
      this.issues = [...this.issues, ...c];
    };
    const n = new.target.prototype;
    Object.setPrototypeOf ? Object.setPrototypeOf(this, n) : this.__proto__ = n, this.name = "ZodError", this.issues = t;
  }
  format(t) {
    const n = t || function(m) {
      return m.message;
    }, c = { _errors: [] }, l = (m) => {
      for (const _ of m.issues)
        if (_.code === "invalid_union")
          _.unionErrors.map(l);
        else if (_.code === "invalid_return_type")
          l(_.returnTypeError);
        else if (_.code === "invalid_arguments")
          l(_.argumentsError);
        else if (_.path.length === 0)
          c._errors.push(n(_));
        else {
          let f = c, b = 0;
          for (; b < _.path.length; ) {
            const w = _.path[b];
            b === _.path.length - 1 ? (f[w] = f[w] || { _errors: [] }, f[w]._errors.push(n(_))) : f[w] = f[w] || { _errors: [] }, f = f[w], b++;
          }
        }
    };
    return l(this), c;
  }
  static assert(t) {
    if (!(t instanceof Z))
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
    const n = {}, c = [];
    for (const l of this.issues)
      if (l.path.length > 0) {
        const m = l.path[0];
        n[m] = n[m] || [], n[m].push(t(l));
      } else
        c.push(t(l));
    return { formErrors: c, fieldErrors: n };
  }
  get formErrors() {
    return this.flatten();
  }
}
Z.create = (i) => new Z(i);
const se = (i, t) => {
  let n;
  switch (i.code) {
    case v.invalid_type:
      i.received === y.undefined ? n = "Required" : n = `Expected ${i.expected}, received ${i.received}`;
      break;
    case v.invalid_literal:
      n = `Invalid literal value, expected ${JSON.stringify(i.expected, R.jsonStringifyReplacer)}`;
      break;
    case v.unrecognized_keys:
      n = `Unrecognized key(s) in object: ${R.joinValues(i.keys, ", ")}`;
      break;
    case v.invalid_union:
      n = "Invalid input";
      break;
    case v.invalid_union_discriminator:
      n = `Invalid discriminator value. Expected ${R.joinValues(i.options)}`;
      break;
    case v.invalid_enum_value:
      n = `Invalid enum value. Expected ${R.joinValues(i.options)}, received '${i.received}'`;
      break;
    case v.invalid_arguments:
      n = "Invalid function arguments";
      break;
    case v.invalid_return_type:
      n = "Invalid function return type";
      break;
    case v.invalid_date:
      n = "Invalid date";
      break;
    case v.invalid_string:
      typeof i.validation == "object" ? "includes" in i.validation ? (n = `Invalid input: must include "${i.validation.includes}"`, typeof i.validation.position == "number" && (n = `${n} at one or more positions greater than or equal to ${i.validation.position}`)) : "startsWith" in i.validation ? n = `Invalid input: must start with "${i.validation.startsWith}"` : "endsWith" in i.validation ? n = `Invalid input: must end with "${i.validation.endsWith}"` : R.assertNever(i.validation) : i.validation !== "regex" ? n = `Invalid ${i.validation}` : n = "Invalid";
      break;
    case v.too_small:
      i.type === "array" ? n = `Array must contain ${i.exact ? "exactly" : i.inclusive ? "at least" : "more than"} ${i.minimum} element(s)` : i.type === "string" ? n = `String must contain ${i.exact ? "exactly" : i.inclusive ? "at least" : "over"} ${i.minimum} character(s)` : i.type === "number" ? n = `Number must be ${i.exact ? "exactly equal to " : i.inclusive ? "greater than or equal to " : "greater than "}${i.minimum}` : i.type === "bigint" ? n = `Number must be ${i.exact ? "exactly equal to " : i.inclusive ? "greater than or equal to " : "greater than "}${i.minimum}` : i.type === "date" ? n = `Date must be ${i.exact ? "exactly equal to " : i.inclusive ? "greater than or equal to " : "greater than "}${new Date(Number(i.minimum))}` : n = "Invalid input";
      break;
    case v.too_big:
      i.type === "array" ? n = `Array must contain ${i.exact ? "exactly" : i.inclusive ? "at most" : "less than"} ${i.maximum} element(s)` : i.type === "string" ? n = `String must contain ${i.exact ? "exactly" : i.inclusive ? "at most" : "under"} ${i.maximum} character(s)` : i.type === "number" ? n = `Number must be ${i.exact ? "exactly" : i.inclusive ? "less than or equal to" : "less than"} ${i.maximum}` : i.type === "bigint" ? n = `BigInt must be ${i.exact ? "exactly" : i.inclusive ? "less than or equal to" : "less than"} ${i.maximum}` : i.type === "date" ? n = `Date must be ${i.exact ? "exactly" : i.inclusive ? "smaller than or equal to" : "smaller than"} ${new Date(Number(i.maximum))}` : n = "Invalid input";
      break;
    case v.custom:
      n = "Invalid input";
      break;
    case v.invalid_intersection_types:
      n = "Intersection results could not be merged";
      break;
    case v.not_multiple_of:
      n = `Number must be a multiple of ${i.multipleOf}`;
      break;
    case v.not_finite:
      n = "Number must be finite";
      break;
    default:
      n = t.defaultError, R.assertNever(i);
  }
  return { message: n };
};
let Te = se;
function ze() {
  return Te;
}
const Ce = (i) => {
  const { data: t, path: n, errorMaps: c, issueData: l } = i, m = [...n, ...l.path || []], _ = {
    ...l,
    path: m
  };
  if (l.message !== void 0)
    return {
      ...l,
      path: m,
      message: l.message
    };
  let f = "";
  const b = c.filter((w) => !!w).slice().reverse();
  for (const w of b)
    f = w(_, { data: t, defaultError: f }).message;
  return {
    ...l,
    path: m,
    message: f
  };
};
function g(i, t) {
  const n = ze(), c = Ce({
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
    ].filter((l) => !!l)
  });
  i.common.issues.push(c);
}
class $ {
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
    const c = [];
    for (const l of n) {
      if (l.status === "aborted")
        return q;
      l.status === "dirty" && t.dirty(), c.push(l.value);
    }
    return { status: t.value, value: c };
  }
  static async mergeObjectAsync(t, n) {
    const c = [];
    for (const l of n) {
      const m = await l.key, _ = await l.value;
      c.push({
        key: m,
        value: _
      });
    }
    return $.mergeObjectSync(t, c);
  }
  static mergeObjectSync(t, n) {
    const c = {};
    for (const l of n) {
      const { key: m, value: _ } = l;
      if (m.status === "aborted" || _.status === "aborted")
        return q;
      m.status === "dirty" && t.dirty(), _.status === "dirty" && t.dirty(), m.value !== "__proto__" && (typeof _.value < "u" || l.alwaysSet) && (c[m.value] = _.value);
    }
    return { status: t.value, value: c };
  }
}
const q = Object.freeze({
  status: "aborted"
}), H = (i) => ({ status: "dirty", value: i }), T = (i) => ({ status: "valid", value: i }), fe = (i) => i.status === "aborted", pe = (i) => i.status === "dirty", D = (i) => i.status === "valid", G = (i) => typeof Promise < "u" && i instanceof Promise;
var x;
(function(i) {
  i.errToObj = (t) => typeof t == "string" ? { message: t } : t || {}, i.toString = (t) => typeof t == "string" ? t : t?.message;
})(x || (x = {}));
class C {
  constructor(t, n, c, l) {
    this._cachedPath = [], this.parent = t, this.data = n, this._path = c, this._key = l;
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
      const n = new Z(i.common.issues);
      return this._error = n, this._error;
    }
  };
};
function O(i) {
  if (!i)
    return {};
  const { errorMap: t, invalid_type_error: n, required_error: c, description: l } = i;
  if (t && (n || c))
    throw new Error(`Can't use "invalid_type_error" or "required_error" in conjunction with custom error map.`);
  return t ? { errorMap: t, description: l } : { errorMap: (_, f) => {
    const { message: b } = i;
    return _.code === "invalid_enum_value" ? { message: b ?? f.defaultError } : typeof f.data > "u" ? { message: b ?? c ?? f.defaultError } : _.code !== "invalid_type" ? { message: f.defaultError } : { message: b ?? n ?? f.defaultError };
  }, description: l };
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
      status: new $(),
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
    const c = this.safeParse(t, n);
    if (c.success)
      return c.data;
    throw c.error;
  }
  safeParse(t, n) {
    const c = {
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
    }, l = this._parseSync({ data: t, path: c.path, parent: c });
    return he(c, l);
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
        const c = this._parseSync({ data: t, path: [], parent: n });
        return D(c) ? {
          value: c.value
        } : {
          issues: n.common.issues
        };
      } catch (c) {
        c?.message?.toLowerCase()?.includes("encountered") && (this["~standard"].async = !0), n.common = {
          issues: [],
          async: !0
        };
      }
    return this._parseAsync({ data: t, path: [], parent: n }).then((c) => D(c) ? {
      value: c.value
    } : {
      issues: n.common.issues
    });
  }
  async parseAsync(t, n) {
    const c = await this.safeParseAsync(t, n);
    if (c.success)
      return c.data;
    throw c.error;
  }
  async safeParseAsync(t, n) {
    const c = {
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
    }, l = this._parse({ data: t, path: c.path, parent: c }), m = await (G(l) ? l : Promise.resolve(l));
    return he(c, m);
  }
  refine(t, n) {
    const c = (l) => typeof n == "string" || typeof n > "u" ? { message: n } : typeof n == "function" ? n(l) : n;
    return this._refinement((l, m) => {
      const _ = t(l), f = () => m.addIssue({
        code: v.custom,
        ...c(l)
      });
      return typeof Promise < "u" && _ instanceof Promise ? _.then((b) => b ? !0 : (f(), !1)) : _ ? !0 : (f(), !1);
    });
  }
  refinement(t, n) {
    return this._refinement((c, l) => t(c) ? !0 : (l.addIssue(typeof n == "function" ? n(c, l) : n), !1));
  }
  _refinement(t) {
    return new F({
      schema: this,
      typeName: k.ZodEffects,
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
    return z.create(this);
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
      typeName: k.ZodEffects,
      effect: { type: "transform", transform: t }
    });
  }
  default(t) {
    const n = typeof t == "function" ? t : () => t;
    return new ce({
      ...O(this._def),
      innerType: this,
      defaultValue: n,
      typeName: k.ZodDefault
    });
  }
  brand() {
    return new nt({
      typeName: k.ZodBranded,
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
      typeName: k.ZodCatch
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
const Ne = /^c[^\s-]{8,}$/i, Ze = /^[0-9a-z]+$/, Ie = /^[0-9A-HJKMNP-TV-Z]{26}$/i, Ve = /^[0-9a-fA-F]{8}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{4}\b-[0-9a-fA-F]{12}$/i, Pe = /^[a-z0-9_-]{21}$/i, Le = /^[A-Za-z0-9-_]+\.[A-Za-z0-9-_]+\.[A-Za-z0-9-_]*$/, Me = /^[-+]?P(?!$)(?:(?:[-+]?\d+Y)|(?:[-+]?\d+[.,]\d+Y$))?(?:(?:[-+]?\d+M)|(?:[-+]?\d+[.,]\d+M$))?(?:(?:[-+]?\d+W)|(?:[-+]?\d+[.,]\d+W$))?(?:(?:[-+]?\d+D)|(?:[-+]?\d+[.,]\d+D$))?(?:T(?=[\d+-])(?:(?:[-+]?\d+H)|(?:[-+]?\d+[.,]\d+H$))?(?:(?:[-+]?\d+M)|(?:[-+]?\d+[.,]\d+M$))?(?:[-+]?\d+(?:[.,]\d+)?S)?)??$/, De = /^(?!\.)(?!.*\.\.)([A-Z0-9_'+\-\.]*)[A-Z0-9_+-]@([A-Z0-9][A-Z0-9\-]*\.)+[A-Z]{2,}$/i, Ue = "^(\\p{Extended_Pictographic}|\\p{Emoji_Component})+$";
let ne;
const Be = /^(?:(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])$/, Fe = /^(?:(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])\.){3}(?:25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9][0-9]|[0-9])\/(3[0-2]|[12]?[0-9])$/, We = /^(([0-9a-fA-F]{1,4}:){7,7}[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,7}:|([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}|([0-9a-fA-F]{1,4}:){1,4}(:[0-9a-fA-F]{1,4}){1,3}|([0-9a-fA-F]{1,4}:){1,3}(:[0-9a-fA-F]{1,4}){1,4}|([0-9a-fA-F]{1,4}:){1,2}(:[0-9a-fA-F]{1,4}){1,5}|[0-9a-fA-F]{1,4}:((:[0-9a-fA-F]{1,4}){1,6})|:((:[0-9a-fA-F]{1,4}){1,7}|:)|fe80:(:[0-9a-fA-F]{0,4}){0,4}%[0-9a-zA-Z]{1,}|::(ffff(:0{1,4}){0,1}:){0,1}((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])|([0-9a-fA-F]{1,4}:){1,4}:((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9]))$/, Je = /^(([0-9a-fA-F]{1,4}:){7,7}[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,7}:|([0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|([0-9a-fA-F]{1,4}:){1,5}(:[0-9a-fA-F]{1,4}){1,2}|([0-9a-fA-F]{1,4}:){1,4}(:[0-9a-fA-F]{1,4}){1,3}|([0-9a-fA-F]{1,4}:){1,3}(:[0-9a-fA-F]{1,4}){1,4}|([0-9a-fA-F]{1,4}:){1,2}(:[0-9a-fA-F]{1,4}){1,5}|[0-9a-fA-F]{1,4}:((:[0-9a-fA-F]{1,4}){1,6})|:((:[0-9a-fA-F]{1,4}){1,7}|:)|fe80:(:[0-9a-fA-F]{0,4}){0,4}%[0-9a-zA-Z]{1,}|::(ffff(:0{1,4}){0,1}:){0,1}((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])|([0-9a-fA-F]{1,4}:){1,4}:((25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9])\.){3,3}(25[0-5]|(2[0-4]|1{0,1}[0-9]){0,1}[0-9]))\/(12[0-8]|1[01][0-9]|[1-9]?[0-9])$/, He = /^([0-9a-zA-Z+/]{4})*(([0-9a-zA-Z+/]{2}==)|([0-9a-zA-Z+/]{3}=))?$/, Ye = /^([0-9a-zA-Z-_]{4})*(([0-9a-zA-Z-_]{2}(==)?)|([0-9a-zA-Z-_]{3}(=)?))?$/, Ae = "((\\d\\d[2468][048]|\\d\\d[13579][26]|\\d\\d0[48]|[02468][048]00|[13579][26]00)-02-29|\\d{4}-((0[13578]|1[02])-(0[1-9]|[12]\\d|3[01])|(0[469]|11)-(0[1-9]|[12]\\d|30)|(02)-(0[1-9]|1\\d|2[0-8])))", Qe = new RegExp(`^${Ae}$`);
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
  return !!((t === "v4" || !t) && Be.test(i) || (t === "v6" || !t) && We.test(i));
}
function et(i, t) {
  if (!Le.test(i))
    return !1;
  try {
    const [n] = i.split(".");
    if (!n)
      return !1;
    const c = n.replace(/-/g, "+").replace(/_/g, "/").padEnd(n.length + (4 - n.length % 4) % 4, "="), l = JSON.parse(atob(c));
    return !(typeof l != "object" || l === null || "typ" in l && l?.typ !== "JWT" || !l.alg || t && l.alg !== t);
  } catch {
    return !1;
  }
}
function tt(i, t) {
  return !!((t === "v4" || !t) && Fe.test(i) || (t === "v6" || !t) && Je.test(i));
}
class N extends A {
  _parse(t) {
    if (this._def.coerce && (t.data = String(t.data)), this._getType(t) !== y.string) {
      const m = this._getOrReturnCtx(t);
      return g(m, {
        code: v.invalid_type,
        expected: y.string,
        received: m.parsedType
      }), q;
    }
    const c = new $();
    let l;
    for (const m of this._def.checks)
      if (m.kind === "min")
        t.data.length < m.value && (l = this._getOrReturnCtx(t, l), g(l, {
          code: v.too_small,
          minimum: m.value,
          type: "string",
          inclusive: !0,
          exact: !1,
          message: m.message
        }), c.dirty());
      else if (m.kind === "max")
        t.data.length > m.value && (l = this._getOrReturnCtx(t, l), g(l, {
          code: v.too_big,
          maximum: m.value,
          type: "string",
          inclusive: !0,
          exact: !1,
          message: m.message
        }), c.dirty());
      else if (m.kind === "length") {
        const _ = t.data.length > m.value, f = t.data.length < m.value;
        (_ || f) && (l = this._getOrReturnCtx(t, l), _ ? g(l, {
          code: v.too_big,
          maximum: m.value,
          type: "string",
          inclusive: !0,
          exact: !0,
          message: m.message
        }) : f && g(l, {
          code: v.too_small,
          minimum: m.value,
          type: "string",
          inclusive: !0,
          exact: !0,
          message: m.message
        }), c.dirty());
      } else if (m.kind === "email")
        De.test(t.data) || (l = this._getOrReturnCtx(t, l), g(l, {
          validation: "email",
          code: v.invalid_string,
          message: m.message
        }), c.dirty());
      else if (m.kind === "emoji")
        ne || (ne = new RegExp(Ue, "u")), ne.test(t.data) || (l = this._getOrReturnCtx(t, l), g(l, {
          validation: "emoji",
          code: v.invalid_string,
          message: m.message
        }), c.dirty());
      else if (m.kind === "uuid")
        Ve.test(t.data) || (l = this._getOrReturnCtx(t, l), g(l, {
          validation: "uuid",
          code: v.invalid_string,
          message: m.message
        }), c.dirty());
      else if (m.kind === "nanoid")
        Pe.test(t.data) || (l = this._getOrReturnCtx(t, l), g(l, {
          validation: "nanoid",
          code: v.invalid_string,
          message: m.message
        }), c.dirty());
      else if (m.kind === "cuid")
        Ne.test(t.data) || (l = this._getOrReturnCtx(t, l), g(l, {
          validation: "cuid",
          code: v.invalid_string,
          message: m.message
        }), c.dirty());
      else if (m.kind === "cuid2")
        Ze.test(t.data) || (l = this._getOrReturnCtx(t, l), g(l, {
          validation: "cuid2",
          code: v.invalid_string,
          message: m.message
        }), c.dirty());
      else if (m.kind === "ulid")
        Ie.test(t.data) || (l = this._getOrReturnCtx(t, l), g(l, {
          validation: "ulid",
          code: v.invalid_string,
          message: m.message
        }), c.dirty());
      else if (m.kind === "url")
        try {
          new URL(t.data);
        } catch {
          l = this._getOrReturnCtx(t, l), g(l, {
            validation: "url",
            code: v.invalid_string,
            message: m.message
          }), c.dirty();
        }
      else m.kind === "regex" ? (m.regex.lastIndex = 0, m.regex.test(t.data) || (l = this._getOrReturnCtx(t, l), g(l, {
        validation: "regex",
        code: v.invalid_string,
        message: m.message
      }), c.dirty())) : m.kind === "trim" ? t.data = t.data.trim() : m.kind === "includes" ? t.data.includes(m.value, m.position) || (l = this._getOrReturnCtx(t, l), g(l, {
        code: v.invalid_string,
        validation: { includes: m.value, position: m.position },
        message: m.message
      }), c.dirty()) : m.kind === "toLowerCase" ? t.data = t.data.toLowerCase() : m.kind === "toUpperCase" ? t.data = t.data.toUpperCase() : m.kind === "startsWith" ? t.data.startsWith(m.value) || (l = this._getOrReturnCtx(t, l), g(l, {
        code: v.invalid_string,
        validation: { startsWith: m.value },
        message: m.message
      }), c.dirty()) : m.kind === "endsWith" ? t.data.endsWith(m.value) || (l = this._getOrReturnCtx(t, l), g(l, {
        code: v.invalid_string,
        validation: { endsWith: m.value },
        message: m.message
      }), c.dirty()) : m.kind === "datetime" ? Xe(m).test(t.data) || (l = this._getOrReturnCtx(t, l), g(l, {
        code: v.invalid_string,
        validation: "datetime",
        message: m.message
      }), c.dirty()) : m.kind === "date" ? Qe.test(t.data) || (l = this._getOrReturnCtx(t, l), g(l, {
        code: v.invalid_string,
        validation: "date",
        message: m.message
      }), c.dirty()) : m.kind === "time" ? Ge(m).test(t.data) || (l = this._getOrReturnCtx(t, l), g(l, {
        code: v.invalid_string,
        validation: "time",
        message: m.message
      }), c.dirty()) : m.kind === "duration" ? Me.test(t.data) || (l = this._getOrReturnCtx(t, l), g(l, {
        validation: "duration",
        code: v.invalid_string,
        message: m.message
      }), c.dirty()) : m.kind === "ip" ? Ke(t.data, m.version) || (l = this._getOrReturnCtx(t, l), g(l, {
        validation: "ip",
        code: v.invalid_string,
        message: m.message
      }), c.dirty()) : m.kind === "jwt" ? et(t.data, m.alg) || (l = this._getOrReturnCtx(t, l), g(l, {
        validation: "jwt",
        code: v.invalid_string,
        message: m.message
      }), c.dirty()) : m.kind === "cidr" ? tt(t.data, m.version) || (l = this._getOrReturnCtx(t, l), g(l, {
        validation: "cidr",
        code: v.invalid_string,
        message: m.message
      }), c.dirty()) : m.kind === "base64" ? He.test(t.data) || (l = this._getOrReturnCtx(t, l), g(l, {
        validation: "base64",
        code: v.invalid_string,
        message: m.message
      }), c.dirty()) : m.kind === "base64url" ? Ye.test(t.data) || (l = this._getOrReturnCtx(t, l), g(l, {
        validation: "base64url",
        code: v.invalid_string,
        message: m.message
      }), c.dirty()) : R.assertNever(m);
    return { status: c.value, value: t.data };
  }
  _regex(t, n, c) {
    return this.refinement((l) => t.test(l), {
      validation: n,
      code: v.invalid_string,
      ...x.errToObj(c)
    });
  }
  _addCheck(t) {
    return new N({
      ...this._def,
      checks: [...this._def.checks, t]
    });
  }
  email(t) {
    return this._addCheck({ kind: "email", ...x.errToObj(t) });
  }
  url(t) {
    return this._addCheck({ kind: "url", ...x.errToObj(t) });
  }
  emoji(t) {
    return this._addCheck({ kind: "emoji", ...x.errToObj(t) });
  }
  uuid(t) {
    return this._addCheck({ kind: "uuid", ...x.errToObj(t) });
  }
  nanoid(t) {
    return this._addCheck({ kind: "nanoid", ...x.errToObj(t) });
  }
  cuid(t) {
    return this._addCheck({ kind: "cuid", ...x.errToObj(t) });
  }
  cuid2(t) {
    return this._addCheck({ kind: "cuid2", ...x.errToObj(t) });
  }
  ulid(t) {
    return this._addCheck({ kind: "ulid", ...x.errToObj(t) });
  }
  base64(t) {
    return this._addCheck({ kind: "base64", ...x.errToObj(t) });
  }
  base64url(t) {
    return this._addCheck({
      kind: "base64url",
      ...x.errToObj(t)
    });
  }
  jwt(t) {
    return this._addCheck({ kind: "jwt", ...x.errToObj(t) });
  }
  ip(t) {
    return this._addCheck({ kind: "ip", ...x.errToObj(t) });
  }
  cidr(t) {
    return this._addCheck({ kind: "cidr", ...x.errToObj(t) });
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
      ...x.errToObj(t?.message)
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
      ...x.errToObj(t?.message)
    });
  }
  duration(t) {
    return this._addCheck({ kind: "duration", ...x.errToObj(t) });
  }
  regex(t, n) {
    return this._addCheck({
      kind: "regex",
      regex: t,
      ...x.errToObj(n)
    });
  }
  includes(t, n) {
    return this._addCheck({
      kind: "includes",
      value: t,
      position: n?.position,
      ...x.errToObj(n?.message)
    });
  }
  startsWith(t, n) {
    return this._addCheck({
      kind: "startsWith",
      value: t,
      ...x.errToObj(n)
    });
  }
  endsWith(t, n) {
    return this._addCheck({
      kind: "endsWith",
      value: t,
      ...x.errToObj(n)
    });
  }
  min(t, n) {
    return this._addCheck({
      kind: "min",
      value: t,
      ...x.errToObj(n)
    });
  }
  max(t, n) {
    return this._addCheck({
      kind: "max",
      value: t,
      ...x.errToObj(n)
    });
  }
  length(t, n) {
    return this._addCheck({
      kind: "length",
      value: t,
      ...x.errToObj(n)
    });
  }
  /**
   * Equivalent to `.min(1)`
   */
  nonempty(t) {
    return this.min(1, x.errToObj(t));
  }
  trim() {
    return new N({
      ...this._def,
      checks: [...this._def.checks, { kind: "trim" }]
    });
  }
  toLowerCase() {
    return new N({
      ...this._def,
      checks: [...this._def.checks, { kind: "toLowerCase" }]
    });
  }
  toUpperCase() {
    return new N({
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
N.create = (i) => new N({
  checks: [],
  typeName: k.ZodString,
  coerce: i?.coerce ?? !1,
  ...O(i)
});
function it(i, t) {
  const n = (i.toString().split(".")[1] || "").length, c = (t.toString().split(".")[1] || "").length, l = n > c ? n : c, m = Number.parseInt(i.toFixed(l).replace(".", "")), _ = Number.parseInt(t.toFixed(l).replace(".", ""));
  return m % _ / 10 ** l;
}
class U extends A {
  constructor() {
    super(...arguments), this.min = this.gte, this.max = this.lte, this.step = this.multipleOf;
  }
  _parse(t) {
    if (this._def.coerce && (t.data = Number(t.data)), this._getType(t) !== y.number) {
      const m = this._getOrReturnCtx(t);
      return g(m, {
        code: v.invalid_type,
        expected: y.number,
        received: m.parsedType
      }), q;
    }
    let c;
    const l = new $();
    for (const m of this._def.checks)
      m.kind === "int" ? R.isInteger(t.data) || (c = this._getOrReturnCtx(t, c), g(c, {
        code: v.invalid_type,
        expected: "integer",
        received: "float",
        message: m.message
      }), l.dirty()) : m.kind === "min" ? (m.inclusive ? t.data < m.value : t.data <= m.value) && (c = this._getOrReturnCtx(t, c), g(c, {
        code: v.too_small,
        minimum: m.value,
        type: "number",
        inclusive: m.inclusive,
        exact: !1,
        message: m.message
      }), l.dirty()) : m.kind === "max" ? (m.inclusive ? t.data > m.value : t.data >= m.value) && (c = this._getOrReturnCtx(t, c), g(c, {
        code: v.too_big,
        maximum: m.value,
        type: "number",
        inclusive: m.inclusive,
        exact: !1,
        message: m.message
      }), l.dirty()) : m.kind === "multipleOf" ? it(t.data, m.value) !== 0 && (c = this._getOrReturnCtx(t, c), g(c, {
        code: v.not_multiple_of,
        multipleOf: m.value,
        message: m.message
      }), l.dirty()) : m.kind === "finite" ? Number.isFinite(t.data) || (c = this._getOrReturnCtx(t, c), g(c, {
        code: v.not_finite,
        message: m.message
      }), l.dirty()) : R.assertNever(m);
    return { status: l.value, value: t.data };
  }
  gte(t, n) {
    return this.setLimit("min", t, !0, x.toString(n));
  }
  gt(t, n) {
    return this.setLimit("min", t, !1, x.toString(n));
  }
  lte(t, n) {
    return this.setLimit("max", t, !0, x.toString(n));
  }
  lt(t, n) {
    return this.setLimit("max", t, !1, x.toString(n));
  }
  setLimit(t, n, c, l) {
    return new U({
      ...this._def,
      checks: [
        ...this._def.checks,
        {
          kind: t,
          value: n,
          inclusive: c,
          message: x.toString(l)
        }
      ]
    });
  }
  _addCheck(t) {
    return new U({
      ...this._def,
      checks: [...this._def.checks, t]
    });
  }
  int(t) {
    return this._addCheck({
      kind: "int",
      message: x.toString(t)
    });
  }
  positive(t) {
    return this._addCheck({
      kind: "min",
      value: 0,
      inclusive: !1,
      message: x.toString(t)
    });
  }
  negative(t) {
    return this._addCheck({
      kind: "max",
      value: 0,
      inclusive: !1,
      message: x.toString(t)
    });
  }
  nonpositive(t) {
    return this._addCheck({
      kind: "max",
      value: 0,
      inclusive: !0,
      message: x.toString(t)
    });
  }
  nonnegative(t) {
    return this._addCheck({
      kind: "min",
      value: 0,
      inclusive: !0,
      message: x.toString(t)
    });
  }
  multipleOf(t, n) {
    return this._addCheck({
      kind: "multipleOf",
      value: t,
      message: x.toString(n)
    });
  }
  finite(t) {
    return this._addCheck({
      kind: "finite",
      message: x.toString(t)
    });
  }
  safe(t) {
    return this._addCheck({
      kind: "min",
      inclusive: !0,
      value: Number.MIN_SAFE_INTEGER,
      message: x.toString(t)
    })._addCheck({
      kind: "max",
      inclusive: !0,
      value: Number.MAX_SAFE_INTEGER,
      message: x.toString(t)
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
    for (const c of this._def.checks) {
      if (c.kind === "finite" || c.kind === "int" || c.kind === "multipleOf")
        return !0;
      c.kind === "min" ? (n === null || c.value > n) && (n = c.value) : c.kind === "max" && (t === null || c.value < t) && (t = c.value);
    }
    return Number.isFinite(n) && Number.isFinite(t);
  }
}
U.create = (i) => new U({
  checks: [],
  typeName: k.ZodNumber,
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
    if (this._getType(t) !== y.bigint)
      return this._getInvalidInput(t);
    let c;
    const l = new $();
    for (const m of this._def.checks)
      m.kind === "min" ? (m.inclusive ? t.data < m.value : t.data <= m.value) && (c = this._getOrReturnCtx(t, c), g(c, {
        code: v.too_small,
        type: "bigint",
        minimum: m.value,
        inclusive: m.inclusive,
        message: m.message
      }), l.dirty()) : m.kind === "max" ? (m.inclusive ? t.data > m.value : t.data >= m.value) && (c = this._getOrReturnCtx(t, c), g(c, {
        code: v.too_big,
        type: "bigint",
        maximum: m.value,
        inclusive: m.inclusive,
        message: m.message
      }), l.dirty()) : m.kind === "multipleOf" ? t.data % m.value !== BigInt(0) && (c = this._getOrReturnCtx(t, c), g(c, {
        code: v.not_multiple_of,
        multipleOf: m.value,
        message: m.message
      }), l.dirty()) : R.assertNever(m);
    return { status: l.value, value: t.data };
  }
  _getInvalidInput(t) {
    const n = this._getOrReturnCtx(t);
    return g(n, {
      code: v.invalid_type,
      expected: y.bigint,
      received: n.parsedType
    }), q;
  }
  gte(t, n) {
    return this.setLimit("min", t, !0, x.toString(n));
  }
  gt(t, n) {
    return this.setLimit("min", t, !1, x.toString(n));
  }
  lte(t, n) {
    return this.setLimit("max", t, !0, x.toString(n));
  }
  lt(t, n) {
    return this.setLimit("max", t, !1, x.toString(n));
  }
  setLimit(t, n, c, l) {
    return new Y({
      ...this._def,
      checks: [
        ...this._def.checks,
        {
          kind: t,
          value: n,
          inclusive: c,
          message: x.toString(l)
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
      message: x.toString(t)
    });
  }
  negative(t) {
    return this._addCheck({
      kind: "max",
      value: BigInt(0),
      inclusive: !1,
      message: x.toString(t)
    });
  }
  nonpositive(t) {
    return this._addCheck({
      kind: "max",
      value: BigInt(0),
      inclusive: !0,
      message: x.toString(t)
    });
  }
  nonnegative(t) {
    return this._addCheck({
      kind: "min",
      value: BigInt(0),
      inclusive: !0,
      message: x.toString(t)
    });
  }
  multipleOf(t, n) {
    return this._addCheck({
      kind: "multipleOf",
      value: t,
      message: x.toString(n)
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
  typeName: k.ZodBigInt,
  coerce: i?.coerce ?? !1,
  ...O(i)
});
class ae extends A {
  _parse(t) {
    if (this._def.coerce && (t.data = !!t.data), this._getType(t) !== y.boolean) {
      const c = this._getOrReturnCtx(t);
      return g(c, {
        code: v.invalid_type,
        expected: y.boolean,
        received: c.parsedType
      }), q;
    }
    return T(t.data);
  }
}
ae.create = (i) => new ae({
  typeName: k.ZodBoolean,
  coerce: i?.coerce || !1,
  ...O(i)
});
class X extends A {
  _parse(t) {
    if (this._def.coerce && (t.data = new Date(t.data)), this._getType(t) !== y.date) {
      const m = this._getOrReturnCtx(t);
      return g(m, {
        code: v.invalid_type,
        expected: y.date,
        received: m.parsedType
      }), q;
    }
    if (Number.isNaN(t.data.getTime())) {
      const m = this._getOrReturnCtx(t);
      return g(m, {
        code: v.invalid_date
      }), q;
    }
    const c = new $();
    let l;
    for (const m of this._def.checks)
      m.kind === "min" ? t.data.getTime() < m.value && (l = this._getOrReturnCtx(t, l), g(l, {
        code: v.too_small,
        message: m.message,
        inclusive: !0,
        exact: !1,
        minimum: m.value,
        type: "date"
      }), c.dirty()) : m.kind === "max" ? t.data.getTime() > m.value && (l = this._getOrReturnCtx(t, l), g(l, {
        code: v.too_big,
        message: m.message,
        inclusive: !0,
        exact: !1,
        maximum: m.value,
        type: "date"
      }), c.dirty()) : R.assertNever(m);
    return {
      status: c.value,
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
      message: x.toString(n)
    });
  }
  max(t, n) {
    return this._addCheck({
      kind: "max",
      value: t.getTime(),
      message: x.toString(n)
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
  typeName: k.ZodDate,
  ...O(i)
});
class ve extends A {
  _parse(t) {
    if (this._getType(t) !== y.symbol) {
      const c = this._getOrReturnCtx(t);
      return g(c, {
        code: v.invalid_type,
        expected: y.symbol,
        received: c.parsedType
      }), q;
    }
    return T(t.data);
  }
}
ve.create = (i) => new ve({
  typeName: k.ZodSymbol,
  ...O(i)
});
class ge extends A {
  _parse(t) {
    if (this._getType(t) !== y.undefined) {
      const c = this._getOrReturnCtx(t);
      return g(c, {
        code: v.invalid_type,
        expected: y.undefined,
        received: c.parsedType
      }), q;
    }
    return T(t.data);
  }
}
ge.create = (i) => new ge({
  typeName: k.ZodUndefined,
  ...O(i)
});
class re extends A {
  _parse(t) {
    if (this._getType(t) !== y.null) {
      const c = this._getOrReturnCtx(t);
      return g(c, {
        code: v.invalid_type,
        expected: y.null,
        received: c.parsedType
      }), q;
    }
    return T(t.data);
  }
}
re.create = (i) => new re({
  typeName: k.ZodNull,
  ...O(i)
});
class ye extends A {
  constructor() {
    super(...arguments), this._any = !0;
  }
  _parse(t) {
    return T(t.data);
  }
}
ye.create = (i) => new ye({
  typeName: k.ZodAny,
  ...O(i)
});
class xe extends A {
  constructor() {
    super(...arguments), this._unknown = !0;
  }
  _parse(t) {
    return T(t.data);
  }
}
xe.create = (i) => new xe({
  typeName: k.ZodUnknown,
  ...O(i)
});
class P extends A {
  _parse(t) {
    const n = this._getOrReturnCtx(t);
    return g(n, {
      code: v.invalid_type,
      expected: y.never,
      received: n.parsedType
    }), q;
  }
}
P.create = (i) => new P({
  typeName: k.ZodNever,
  ...O(i)
});
class be extends A {
  _parse(t) {
    if (this._getType(t) !== y.undefined) {
      const c = this._getOrReturnCtx(t);
      return g(c, {
        code: v.invalid_type,
        expected: y.void,
        received: c.parsedType
      }), q;
    }
    return T(t.data);
  }
}
be.create = (i) => new be({
  typeName: k.ZodVoid,
  ...O(i)
});
class z extends A {
  _parse(t) {
    const { ctx: n, status: c } = this._processInputParams(t), l = this._def;
    if (n.parsedType !== y.array)
      return g(n, {
        code: v.invalid_type,
        expected: y.array,
        received: n.parsedType
      }), q;
    if (l.exactLength !== null) {
      const _ = n.data.length > l.exactLength.value, f = n.data.length < l.exactLength.value;
      (_ || f) && (g(n, {
        code: _ ? v.too_big : v.too_small,
        minimum: f ? l.exactLength.value : void 0,
        maximum: _ ? l.exactLength.value : void 0,
        type: "array",
        inclusive: !0,
        exact: !0,
        message: l.exactLength.message
      }), c.dirty());
    }
    if (l.minLength !== null && n.data.length < l.minLength.value && (g(n, {
      code: v.too_small,
      minimum: l.minLength.value,
      type: "array",
      inclusive: !0,
      exact: !1,
      message: l.minLength.message
    }), c.dirty()), l.maxLength !== null && n.data.length > l.maxLength.value && (g(n, {
      code: v.too_big,
      maximum: l.maxLength.value,
      type: "array",
      inclusive: !0,
      exact: !1,
      message: l.maxLength.message
    }), c.dirty()), n.common.async)
      return Promise.all([...n.data].map((_, f) => l.type._parseAsync(new C(n, _, n.path, f)))).then((_) => $.mergeArray(c, _));
    const m = [...n.data].map((_, f) => l.type._parseSync(new C(n, _, n.path, f)));
    return $.mergeArray(c, m);
  }
  get element() {
    return this._def.type;
  }
  min(t, n) {
    return new z({
      ...this._def,
      minLength: { value: t, message: x.toString(n) }
    });
  }
  max(t, n) {
    return new z({
      ...this._def,
      maxLength: { value: t, message: x.toString(n) }
    });
  }
  length(t, n) {
    return new z({
      ...this._def,
      exactLength: { value: t, message: x.toString(n) }
    });
  }
  nonempty(t) {
    return this.min(1, t);
  }
}
z.create = (i, t) => new z({
  type: i,
  minLength: null,
  maxLength: null,
  exactLength: null,
  typeName: k.ZodArray,
  ...O(t)
});
function M(i) {
  if (i instanceof j) {
    const t = {};
    for (const n in i.shape) {
      const c = i.shape[n];
      t[n] = V.create(M(c));
    }
    return new j({
      ...i._def,
      shape: () => t
    });
  } else return i instanceof z ? new z({
    ...i._def,
    type: M(i.element)
  }) : i instanceof V ? V.create(M(i.unwrap())) : i instanceof W ? W.create(M(i.unwrap())) : i instanceof L ? L.create(i.items.map((t) => M(t))) : i;
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
    if (this._getType(t) !== y.object) {
      const w = this._getOrReturnCtx(t);
      return g(w, {
        code: v.invalid_type,
        expected: y.object,
        received: w.parsedType
      }), q;
    }
    const { status: c, ctx: l } = this._processInputParams(t), { shape: m, keys: _ } = this._getCached(), f = [];
    if (!(this._def.catchall instanceof P && this._def.unknownKeys === "strip"))
      for (const w in l.data)
        _.includes(w) || f.push(w);
    const b = [];
    for (const w of _) {
      const E = m[w], J = l.data[w];
      b.push({
        key: { status: "valid", value: w },
        value: E._parse(new C(l, J, l.path, w)),
        alwaysSet: w in l.data
      });
    }
    if (this._def.catchall instanceof P) {
      const w = this._def.unknownKeys;
      if (w === "passthrough")
        for (const E of f)
          b.push({
            key: { status: "valid", value: E },
            value: { status: "valid", value: l.data[E] }
          });
      else if (w === "strict")
        f.length > 0 && (g(l, {
          code: v.unrecognized_keys,
          keys: f
        }), c.dirty());
      else if (w !== "strip") throw new Error("Internal ZodObject error: invalid unknownKeys value.");
    } else {
      const w = this._def.catchall;
      for (const E of f) {
        const J = l.data[E];
        b.push({
          key: { status: "valid", value: E },
          value: w._parse(
            new C(l, J, l.path, E)
            //, ctx.child(key), value, getParsedType(value)
          ),
          alwaysSet: E in l.data
        });
      }
    }
    return l.common.async ? Promise.resolve().then(async () => {
      const w = [];
      for (const E of b) {
        const J = await E.key, $e = await E.value;
        w.push({
          key: J,
          value: $e,
          alwaysSet: E.alwaysSet
        });
      }
      return w;
    }).then((w) => $.mergeObjectSync(c, w)) : $.mergeObjectSync(c, b);
  }
  get shape() {
    return this._def.shape();
  }
  strict(t) {
    return x.errToObj, new j({
      ...this._def,
      unknownKeys: "strict",
      ...t !== void 0 ? {
        errorMap: (n, c) => {
          const l = this._def.errorMap?.(n, c).message ?? c.defaultError;
          return n.code === "unrecognized_keys" ? {
            message: x.errToObj(t).message ?? l
          } : {
            message: l
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
      typeName: k.ZodObject
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
    for (const c of R.objectKeys(t))
      t[c] && this.shape[c] && (n[c] = this.shape[c]);
    return new j({
      ...this._def,
      shape: () => n
    });
  }
  omit(t) {
    const n = {};
    for (const c of R.objectKeys(this.shape))
      t[c] || (n[c] = this.shape[c]);
    return new j({
      ...this._def,
      shape: () => n
    });
  }
  /**
   * @deprecated
   */
  deepPartial() {
    return M(this);
  }
  partial(t) {
    const n = {};
    for (const c of R.objectKeys(this.shape)) {
      const l = this.shape[c];
      t && !t[c] ? n[c] = l : n[c] = l.optional();
    }
    return new j({
      ...this._def,
      shape: () => n
    });
  }
  required(t) {
    const n = {};
    for (const c of R.objectKeys(this.shape))
      if (t && !t[c])
        n[c] = this.shape[c];
      else {
        let m = this.shape[c];
        for (; m instanceof V; )
          m = m._def.innerType;
        n[c] = m;
      }
    return new j({
      ...this._def,
      shape: () => n
    });
  }
  keyof() {
    return Ee(R.objectKeys(this.shape));
  }
}
j.create = (i, t) => new j({
  shape: () => i,
  unknownKeys: "strip",
  catchall: P.create(),
  typeName: k.ZodObject,
  ...O(t)
});
j.strictCreate = (i, t) => new j({
  shape: () => i,
  unknownKeys: "strict",
  catchall: P.create(),
  typeName: k.ZodObject,
  ...O(t)
});
j.lazycreate = (i, t) => new j({
  shape: i,
  unknownKeys: "strip",
  catchall: P.create(),
  typeName: k.ZodObject,
  ...O(t)
});
class K extends A {
  _parse(t) {
    const { ctx: n } = this._processInputParams(t), c = this._def.options;
    function l(m) {
      for (const f of m)
        if (f.result.status === "valid")
          return f.result;
      for (const f of m)
        if (f.result.status === "dirty")
          return n.common.issues.push(...f.ctx.common.issues), f.result;
      const _ = m.map((f) => new Z(f.ctx.common.issues));
      return g(n, {
        code: v.invalid_union,
        unionErrors: _
      }), q;
    }
    if (n.common.async)
      return Promise.all(c.map(async (m) => {
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
      })).then(l);
    {
      let m;
      const _ = [];
      for (const b of c) {
        const w = {
          ...n,
          common: {
            ...n.common,
            issues: []
          },
          parent: null
        }, E = b._parseSync({
          data: n.data,
          path: n.path,
          parent: w
        });
        if (E.status === "valid")
          return E;
        E.status === "dirty" && !m && (m = { result: E, ctx: w }), w.common.issues.length && _.push(w.common.issues);
      }
      if (m)
        return n.common.issues.push(...m.ctx.common.issues), m.result;
      const f = _.map((b) => new Z(b));
      return g(n, {
        code: v.invalid_union,
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
  typeName: k.ZodUnion,
  ...O(t)
});
function de(i, t) {
  const n = I(i), c = I(t);
  if (i === t)
    return { valid: !0, data: i };
  if (n === y.object && c === y.object) {
    const l = R.objectKeys(t), m = R.objectKeys(i).filter((f) => l.indexOf(f) !== -1), _ = { ...i, ...t };
    for (const f of m) {
      const b = de(i[f], t[f]);
      if (!b.valid)
        return { valid: !1 };
      _[f] = b.data;
    }
    return { valid: !0, data: _ };
  } else if (n === y.array && c === y.array) {
    if (i.length !== t.length)
      return { valid: !1 };
    const l = [];
    for (let m = 0; m < i.length; m++) {
      const _ = i[m], f = t[m], b = de(_, f);
      if (!b.valid)
        return { valid: !1 };
      l.push(b.data);
    }
    return { valid: !0, data: l };
  } else return n === y.date && c === y.date && +i == +t ? { valid: !0, data: i } : { valid: !1 };
}
class ee extends A {
  _parse(t) {
    const { status: n, ctx: c } = this._processInputParams(t), l = (m, _) => {
      if (fe(m) || fe(_))
        return q;
      const f = de(m.value, _.value);
      return f.valid ? ((pe(m) || pe(_)) && n.dirty(), { status: n.value, value: f.data }) : (g(c, {
        code: v.invalid_intersection_types
      }), q);
    };
    return c.common.async ? Promise.all([
      this._def.left._parseAsync({
        data: c.data,
        path: c.path,
        parent: c
      }),
      this._def.right._parseAsync({
        data: c.data,
        path: c.path,
        parent: c
      })
    ]).then(([m, _]) => l(m, _)) : l(this._def.left._parseSync({
      data: c.data,
      path: c.path,
      parent: c
    }), this._def.right._parseSync({
      data: c.data,
      path: c.path,
      parent: c
    }));
  }
}
ee.create = (i, t, n) => new ee({
  left: i,
  right: t,
  typeName: k.ZodIntersection,
  ...O(n)
});
class L extends A {
  _parse(t) {
    const { status: n, ctx: c } = this._processInputParams(t);
    if (c.parsedType !== y.array)
      return g(c, {
        code: v.invalid_type,
        expected: y.array,
        received: c.parsedType
      }), q;
    if (c.data.length < this._def.items.length)
      return g(c, {
        code: v.too_small,
        minimum: this._def.items.length,
        inclusive: !0,
        exact: !1,
        type: "array"
      }), q;
    !this._def.rest && c.data.length > this._def.items.length && (g(c, {
      code: v.too_big,
      maximum: this._def.items.length,
      inclusive: !0,
      exact: !1,
      type: "array"
    }), n.dirty());
    const m = [...c.data].map((_, f) => {
      const b = this._def.items[f] || this._def.rest;
      return b ? b._parse(new C(c, _, c.path, f)) : null;
    }).filter((_) => !!_);
    return c.common.async ? Promise.all(m).then((_) => $.mergeArray(n, _)) : $.mergeArray(n, m);
  }
  get items() {
    return this._def.items;
  }
  rest(t) {
    return new L({
      ...this._def,
      rest: t
    });
  }
}
L.create = (i, t) => {
  if (!Array.isArray(i))
    throw new Error("You must pass an array of schemas to z.tuple([ ... ])");
  return new L({
    items: i,
    typeName: k.ZodTuple,
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
    const { status: n, ctx: c } = this._processInputParams(t);
    if (c.parsedType !== y.object)
      return g(c, {
        code: v.invalid_type,
        expected: y.object,
        received: c.parsedType
      }), q;
    const l = [], m = this._def.keyType, _ = this._def.valueType;
    for (const f in c.data)
      l.push({
        key: m._parse(new C(c, f, c.path, f)),
        value: _._parse(new C(c, c.data[f], c.path, f)),
        alwaysSet: f in c.data
      });
    return c.common.async ? $.mergeObjectAsync(n, l) : $.mergeObjectSync(n, l);
  }
  get element() {
    return this._def.valueType;
  }
  static create(t, n, c) {
    return n instanceof A ? new te({
      keyType: t,
      valueType: n,
      typeName: k.ZodRecord,
      ...O(c)
    }) : new te({
      keyType: N.create(),
      valueType: t,
      typeName: k.ZodRecord,
      ...O(n)
    });
  }
}
class we extends A {
  get keySchema() {
    return this._def.keyType;
  }
  get valueSchema() {
    return this._def.valueType;
  }
  _parse(t) {
    const { status: n, ctx: c } = this._processInputParams(t);
    if (c.parsedType !== y.map)
      return g(c, {
        code: v.invalid_type,
        expected: y.map,
        received: c.parsedType
      }), q;
    const l = this._def.keyType, m = this._def.valueType, _ = [...c.data.entries()].map(([f, b], w) => ({
      key: l._parse(new C(c, f, c.path, [w, "key"])),
      value: m._parse(new C(c, b, c.path, [w, "value"]))
    }));
    if (c.common.async) {
      const f = /* @__PURE__ */ new Map();
      return Promise.resolve().then(async () => {
        for (const b of _) {
          const w = await b.key, E = await b.value;
          if (w.status === "aborted" || E.status === "aborted")
            return q;
          (w.status === "dirty" || E.status === "dirty") && n.dirty(), f.set(w.value, E.value);
        }
        return { status: n.value, value: f };
      });
    } else {
      const f = /* @__PURE__ */ new Map();
      for (const b of _) {
        const w = b.key, E = b.value;
        if (w.status === "aborted" || E.status === "aborted")
          return q;
        (w.status === "dirty" || E.status === "dirty") && n.dirty(), f.set(w.value, E.value);
      }
      return { status: n.value, value: f };
    }
  }
}
we.create = (i, t, n) => new we({
  valueType: t,
  keyType: i,
  typeName: k.ZodMap,
  ...O(n)
});
class Q extends A {
  _parse(t) {
    const { status: n, ctx: c } = this._processInputParams(t);
    if (c.parsedType !== y.set)
      return g(c, {
        code: v.invalid_type,
        expected: y.set,
        received: c.parsedType
      }), q;
    const l = this._def;
    l.minSize !== null && c.data.size < l.minSize.value && (g(c, {
      code: v.too_small,
      minimum: l.minSize.value,
      type: "set",
      inclusive: !0,
      exact: !1,
      message: l.minSize.message
    }), n.dirty()), l.maxSize !== null && c.data.size > l.maxSize.value && (g(c, {
      code: v.too_big,
      maximum: l.maxSize.value,
      type: "set",
      inclusive: !0,
      exact: !1,
      message: l.maxSize.message
    }), n.dirty());
    const m = this._def.valueType;
    function _(b) {
      const w = /* @__PURE__ */ new Set();
      for (const E of b) {
        if (E.status === "aborted")
          return q;
        E.status === "dirty" && n.dirty(), w.add(E.value);
      }
      return { status: n.value, value: w };
    }
    const f = [...c.data.values()].map((b, w) => m._parse(new C(c, b, c.path, w)));
    return c.common.async ? Promise.all(f).then((b) => _(b)) : _(f);
  }
  min(t, n) {
    return new Q({
      ...this._def,
      minSize: { value: t, message: x.toString(n) }
    });
  }
  max(t, n) {
    return new Q({
      ...this._def,
      maxSize: { value: t, message: x.toString(n) }
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
  typeName: k.ZodSet,
  ...O(t)
});
class ke extends A {
  get schema() {
    return this._def.getter();
  }
  _parse(t) {
    const { ctx: n } = this._processInputParams(t);
    return this._def.getter()._parse({ data: n.data, path: n.path, parent: n });
  }
}
ke.create = (i, t) => new ke({
  getter: i,
  typeName: k.ZodLazy,
  ...O(t)
});
class oe extends A {
  _parse(t) {
    if (t.data !== this._def.value) {
      const n = this._getOrReturnCtx(t);
      return g(n, {
        received: n.data,
        code: v.invalid_literal,
        expected: this._def.value
      }), q;
    }
    return { status: "valid", value: t.data };
  }
  get value() {
    return this._def.value;
  }
}
oe.create = (i, t) => new oe({
  value: i,
  typeName: k.ZodLiteral,
  ...O(t)
});
function Ee(i, t) {
  return new B({
    values: i,
    typeName: k.ZodEnum,
    ...O(t)
  });
}
class B extends A {
  _parse(t) {
    if (typeof t.data != "string") {
      const n = this._getOrReturnCtx(t), c = this._def.values;
      return g(n, {
        expected: R.joinValues(c),
        received: n.parsedType,
        code: v.invalid_type
      }), q;
    }
    if (this._cache || (this._cache = new Set(this._def.values)), !this._cache.has(t.data)) {
      const n = this._getOrReturnCtx(t), c = this._def.values;
      return g(n, {
        received: n.data,
        code: v.invalid_enum_value,
        options: c
      }), q;
    }
    return T(t.data);
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
    return B.create(t, {
      ...this._def,
      ...n
    });
  }
  exclude(t, n = this._def) {
    return B.create(this.options.filter((c) => !t.includes(c)), {
      ...this._def,
      ...n
    });
  }
}
B.create = Ee;
class qe extends A {
  _parse(t) {
    const n = R.getValidEnumValues(this._def.values), c = this._getOrReturnCtx(t);
    if (c.parsedType !== y.string && c.parsedType !== y.number) {
      const l = R.objectValues(n);
      return g(c, {
        expected: R.joinValues(l),
        received: c.parsedType,
        code: v.invalid_type
      }), q;
    }
    if (this._cache || (this._cache = new Set(R.getValidEnumValues(this._def.values))), !this._cache.has(t.data)) {
      const l = R.objectValues(n);
      return g(c, {
        received: c.data,
        code: v.invalid_enum_value,
        options: l
      }), q;
    }
    return T(t.data);
  }
  get enum() {
    return this._def.values;
  }
}
qe.create = (i, t) => new qe({
  values: i,
  typeName: k.ZodNativeEnum,
  ...O(t)
});
class ie extends A {
  unwrap() {
    return this._def.type;
  }
  _parse(t) {
    const { ctx: n } = this._processInputParams(t);
    if (n.parsedType !== y.promise && n.common.async === !1)
      return g(n, {
        code: v.invalid_type,
        expected: y.promise,
        received: n.parsedType
      }), q;
    const c = n.parsedType === y.promise ? n.data : Promise.resolve(n.data);
    return T(c.then((l) => this._def.type.parseAsync(l, {
      path: n.path,
      errorMap: n.common.contextualErrorMap
    })));
  }
}
ie.create = (i, t) => new ie({
  type: i,
  typeName: k.ZodPromise,
  ...O(t)
});
class F extends A {
  innerType() {
    return this._def.schema;
  }
  sourceType() {
    return this._def.schema._def.typeName === k.ZodEffects ? this._def.schema.sourceType() : this._def.schema;
  }
  _parse(t) {
    const { status: n, ctx: c } = this._processInputParams(t), l = this._def.effect || null, m = {
      addIssue: (_) => {
        g(c, _), _.fatal ? n.abort() : n.dirty();
      },
      get path() {
        return c.path;
      }
    };
    if (m.addIssue = m.addIssue.bind(m), l.type === "preprocess") {
      const _ = l.transform(c.data, m);
      if (c.common.async)
        return Promise.resolve(_).then(async (f) => {
          if (n.value === "aborted")
            return q;
          const b = await this._def.schema._parseAsync({
            data: f,
            path: c.path,
            parent: c
          });
          return b.status === "aborted" ? q : b.status === "dirty" || n.value === "dirty" ? H(b.value) : b;
        });
      {
        if (n.value === "aborted")
          return q;
        const f = this._def.schema._parseSync({
          data: _,
          path: c.path,
          parent: c
        });
        return f.status === "aborted" ? q : f.status === "dirty" || n.value === "dirty" ? H(f.value) : f;
      }
    }
    if (l.type === "refinement") {
      const _ = (f) => {
        const b = l.refinement(f, m);
        if (c.common.async)
          return Promise.resolve(b);
        if (b instanceof Promise)
          throw new Error("Async refinement encountered during synchronous parse operation. Use .parseAsync instead.");
        return f;
      };
      if (c.common.async === !1) {
        const f = this._def.schema._parseSync({
          data: c.data,
          path: c.path,
          parent: c
        });
        return f.status === "aborted" ? q : (f.status === "dirty" && n.dirty(), _(f.value), { status: n.value, value: f.value });
      } else
        return this._def.schema._parseAsync({ data: c.data, path: c.path, parent: c }).then((f) => f.status === "aborted" ? q : (f.status === "dirty" && n.dirty(), _(f.value).then(() => ({ status: n.value, value: f.value }))));
    }
    if (l.type === "transform")
      if (c.common.async === !1) {
        const _ = this._def.schema._parseSync({
          data: c.data,
          path: c.path,
          parent: c
        });
        if (!D(_))
          return q;
        const f = l.transform(_.value, m);
        if (f instanceof Promise)
          throw new Error("Asynchronous transform encountered during synchronous parse operation. Use .parseAsync instead.");
        return { status: n.value, value: f };
      } else
        return this._def.schema._parseAsync({ data: c.data, path: c.path, parent: c }).then((_) => D(_) ? Promise.resolve(l.transform(_.value, m)).then((f) => ({
          status: n.value,
          value: f
        })) : q);
    R.assertNever(l);
  }
}
F.create = (i, t, n) => new F({
  schema: i,
  typeName: k.ZodEffects,
  effect: t,
  ...O(n)
});
F.createWithPreprocess = (i, t, n) => new F({
  schema: t,
  effect: { type: "preprocess", transform: i },
  typeName: k.ZodEffects,
  ...O(n)
});
class V extends A {
  _parse(t) {
    return this._getType(t) === y.undefined ? T(void 0) : this._def.innerType._parse(t);
  }
  unwrap() {
    return this._def.innerType;
  }
}
V.create = (i, t) => new V({
  innerType: i,
  typeName: k.ZodOptional,
  ...O(t)
});
class W extends A {
  _parse(t) {
    return this._getType(t) === y.null ? T(null) : this._def.innerType._parse(t);
  }
  unwrap() {
    return this._def.innerType;
  }
}
W.create = (i, t) => new W({
  innerType: i,
  typeName: k.ZodNullable,
  ...O(t)
});
class ce extends A {
  _parse(t) {
    const { ctx: n } = this._processInputParams(t);
    let c = n.data;
    return n.parsedType === y.undefined && (c = this._def.defaultValue()), this._def.innerType._parse({
      data: c,
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
  typeName: k.ZodDefault,
  defaultValue: typeof t.default == "function" ? t.default : () => t.default,
  ...O(t)
});
class ue extends A {
  _parse(t) {
    const { ctx: n } = this._processInputParams(t), c = {
      ...n,
      common: {
        ...n.common,
        issues: []
      }
    }, l = this._def.innerType._parse({
      data: c.data,
      path: c.path,
      parent: {
        ...c
      }
    });
    return G(l) ? l.then((m) => ({
      status: "valid",
      value: m.status === "valid" ? m.value : this._def.catchValue({
        get error() {
          return new Z(c.common.issues);
        },
        input: c.data
      })
    })) : {
      status: "valid",
      value: l.status === "valid" ? l.value : this._def.catchValue({
        get error() {
          return new Z(c.common.issues);
        },
        input: c.data
      })
    };
  }
  removeCatch() {
    return this._def.innerType;
  }
}
ue.create = (i, t) => new ue({
  innerType: i,
  typeName: k.ZodCatch,
  catchValue: typeof t.catch == "function" ? t.catch : () => t.catch,
  ...O(t)
});
class Oe extends A {
  _parse(t) {
    if (this._getType(t) !== y.nan) {
      const c = this._getOrReturnCtx(t);
      return g(c, {
        code: v.invalid_type,
        expected: y.nan,
        received: c.parsedType
      }), q;
    }
    return { status: "valid", value: t.data };
  }
}
Oe.create = (i) => new Oe({
  typeName: k.ZodNaN,
  ...O(i)
});
class nt extends A {
  _parse(t) {
    const { ctx: n } = this._processInputParams(t), c = n.data;
    return this._def.type._parse({
      data: c,
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
    const { status: n, ctx: c } = this._processInputParams(t);
    if (c.common.async)
      return (async () => {
        const m = await this._def.in._parseAsync({
          data: c.data,
          path: c.path,
          parent: c
        });
        return m.status === "aborted" ? q : m.status === "dirty" ? (n.dirty(), H(m.value)) : this._def.out._parseAsync({
          data: m.value,
          path: c.path,
          parent: c
        });
      })();
    {
      const l = this._def.in._parseSync({
        data: c.data,
        path: c.path,
        parent: c
      });
      return l.status === "aborted" ? q : l.status === "dirty" ? (n.dirty(), {
        status: "dirty",
        value: l.value
      }) : this._def.out._parseSync({
        data: l.value,
        path: c.path,
        parent: c
      });
    }
  }
  static create(t, n) {
    return new me({
      in: t,
      out: n,
      typeName: k.ZodPipeline
    });
  }
}
class le extends A {
  _parse(t) {
    const n = this._def.innerType._parse(t), c = (l) => (D(l) && (l.value = Object.freeze(l.value)), l);
    return G(n) ? n.then((l) => c(l)) : c(n);
  }
  unwrap() {
    return this._def.innerType;
  }
}
le.create = (i, t) => new le({
  innerType: i,
  typeName: k.ZodReadonly,
  ...O(t)
});
var k;
(function(i) {
  i.ZodString = "ZodString", i.ZodNumber = "ZodNumber", i.ZodNaN = "ZodNaN", i.ZodBigInt = "ZodBigInt", i.ZodBoolean = "ZodBoolean", i.ZodDate = "ZodDate", i.ZodSymbol = "ZodSymbol", i.ZodUndefined = "ZodUndefined", i.ZodNull = "ZodNull", i.ZodAny = "ZodAny", i.ZodUnknown = "ZodUnknown", i.ZodNever = "ZodNever", i.ZodVoid = "ZodVoid", i.ZodArray = "ZodArray", i.ZodObject = "ZodObject", i.ZodUnion = "ZodUnion", i.ZodDiscriminatedUnion = "ZodDiscriminatedUnion", i.ZodIntersection = "ZodIntersection", i.ZodTuple = "ZodTuple", i.ZodRecord = "ZodRecord", i.ZodMap = "ZodMap", i.ZodSet = "ZodSet", i.ZodFunction = "ZodFunction", i.ZodLazy = "ZodLazy", i.ZodLiteral = "ZodLiteral", i.ZodEnum = "ZodEnum", i.ZodEffects = "ZodEffects", i.ZodNativeEnum = "ZodNativeEnum", i.ZodOptional = "ZodOptional", i.ZodNullable = "ZodNullable", i.ZodDefault = "ZodDefault", i.ZodCatch = "ZodCatch", i.ZodPromise = "ZodPromise", i.ZodBranded = "ZodBranded", i.ZodPipeline = "ZodPipeline", i.ZodReadonly = "ZodReadonly";
})(k || (k = {}));
const e = N.create, r = U.create, p = ae.create, a = re.create;
P.create;
const o = z.create, u = j.create, s = K.create;
ee.create;
L.create;
const S = te.create, h = oe.create, d = B.create;
ie.create;
V.create;
W.create;
u({ code: e().min(1).max(128), message: e().min(1).max(4e3), retryable: p() }).strict();
const ht = u({ schema_version: h("dsh-browser-status.v1"), ready: p(), runtime_mode: d(["live", "replay"]), interaction_mode: d(["interactive", "read_only"]), run_id: s([e().min(1).max(128), a()]), dsh_session_id: s([e().min(1).max(256), a()]), state: s([d(["admitted", "running", "idle", "completed", "failed", "cancelled", "unknown"]), a()]), error_code: s([e().max(128), a()]), decision_desk_url: e().url().max(2048), business: s([u({ schema_version: h("dsh-business-status.v1"), status: d(["queued", "researching", "completed", "degraded", "research_only", "rejected", "failed", "cancelled"]), gate_status: s([h("publish"), h("degraded"), h("research_only"), h("reject"), h(null)]), coverage_status: s([h("insufficient"), h("sufficient"), h("bounded_stop"), h(null)]), hard_coverage_ratio: s([r().gte(0).lte(1), a()]), stop_reason_code: s([e().max(128), a()]), stop_reason_detail: s([e().max(2e3), a()]), failures: o(u({ capability_id: e().min(1).max(256), error_code: e().min(1).max(128), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e().max(128), a()]), retryable: p() }).strict()).max(16) }).strict(), a()]) }).strict();
u({ capability_id: e().min(1).max(256), error_code: e().min(1).max(128), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e().max(128), a()]), retryable: p() }).strict();
const vt = u({ schema_version: h("dsh-business-status.v1"), status: d(["queued", "researching", "completed", "degraded", "research_only", "rejected", "failed", "cancelled"]), gate_status: s([h("publish"), h("degraded"), h("research_only"), h("reject"), h(null)]), coverage_status: s([h("insufficient"), h("sufficient"), h("bounded_stop"), h(null)]), hard_coverage_ratio: s([r().gte(0).lte(1), a()]), stop_reason_code: s([e().max(128), a()]), stop_reason_detail: s([e().max(2e3), a()]), failures: o(u({ capability_id: e().min(1).max(256), error_code: e().min(1).max(128), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e().max(128), a()]), retryable: p() }).strict()).max(16) }).strict(), gt = u({ schema_version: h("dsh-host-readiness.v1"), ready: p(), version_compatible: p(), session_controller: p(), client_plugin: p(), hub_reachable: p(), upstream_identity: u({ source_commit: e().regex(new RegExp("^[a-f0-9]{40}$")), source_version: e().min(1).max(128), package_versions: S(e().min(1).max(128)), plugin_build_hash: e().regex(new RegExp("^[a-f0-9]{64}$")) }).strict(), checked_at: e().datetime({ offset: !0 }), error_code: s([e().max(128), a()]) }).strict(), yt = u({ schema_version: h("dsh-research-intake.v1"), text: e().min(1).max(2e5), source_id: e().min(1).max(128), language: e().min(2).max(16), client_session_id: s([e().max(256), a()]) }).strict(), xt = u({ schema_version: h("dsh-research-intake-accepted.v1"), event_id: e().min(1).max(128), run_id: e().min(1).max(128), status: h("queued"), status_url: e().min(1).max(2048), decision_desk_url: e().url().max(2048) }).strict(), bt = u({ schema_version: h("dsh-run-session-link.v1"), run_id: e().min(1).max(128), dsh_session_id: e().min(1).max(256), request_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), state: d(["admitted", "running", "idle", "completed", "failed", "cancelled", "unknown"]), generation: r().int().gte(1), upstream_identity: u({ source_commit: e().regex(new RegExp("^[a-f0-9]{40}$")), source_version: e().min(1).max(128), package_versions: S(e().min(1).max(128)), plugin_build_hash: e().regex(new RegExp("^[a-f0-9]{64}$")) }).strict(), deadline_at: s([e().datetime({ offset: !0 }), a()]), max_tool_calls: s([r().int().gte(1).lte(100), a()]), tool_calls_started: r().int().gte(0), accepted_at: s([e().datetime({ offset: !0 }), a()]), last_seen_at: s([e().datetime({ offset: !0 }), a()]), terminal_at: s([e().datetime({ offset: !0 }), a()]), last_seq: r().int().gte(0), trace_ref: s([e().max(2048), a()]), result_ref: s([e().max(2048), a()]), result_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), error_code: s([e().max(128), a()]), created_at: e().datetime({ offset: !0 }), updated_at: e().datetime({ offset: !0 }) }).strict(), wt = u({ schema_version: h("dsh-session-accepted.v1"), run_id: e().min(1).max(128), dsh_session_id: e().min(1).max(256), accepted_at: e().datetime({ offset: !0 }), generation: r().int().gte(1) }).strict(), kt = u({ schema_version: h("dsh-session-completion.v1"), run_id: e().min(1).max(128), dsh_session_id: e().min(1).max(256), terminal_status: d(["completed", "failed", "cancelled"]), generation: r().int().gte(1), last_seq: r().int().gte(0), trace_ref: s([e().max(2048), a()]), result_ref: s([e().max(2048), a()]), result_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), completed_at: e().datetime({ offset: !0 }), error: s([u({ code: e().min(1).max(128), message: e().min(1).max(4e3), retryable: p() }).strict(), a()]) }).strict(), qt = u({ schema_version: h("dsh-session-prompt.v1"), run_id: e().min(1).max(128), dsh_session_id: e().min(1).max(256), request_id: e().min(1).max(256), request_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), generation: r().int().gte(1), prompt: e().min(1).max(1048576) }).strict(), Ot = u({ schema_version: h("dsh-session-result.v1"), run_id: e().min(1).max(128), dsh_session_id: e().min(1).max(256), generation: r().int().gte(1), last_seq: r().int().gte(0), final_response: e().max(1048576), finish_reason: s([e().max(128), a()]), events_json: e().min(2).max(8388608), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), trace_ref: e().min(1).max(2048), result_hash: e().regex(new RegExp("^[a-f0-9]{64}$")) }).strict(), At = u({ schema_version: h("dsh-session-status.v1"), run_id: e().min(1).max(128), dsh_session_id: e().min(1).max(256), state: d(["admitted", "running", "idle", "completed", "failed", "cancelled", "unknown"]), generation: r().int().gte(1), last_seq: r().int().gte(0), observed_at: e().datetime({ offset: !0 }), error_code: s([e().max(128), a()]) }).strict(), Rt = u({ schema_version: h("dsh-session-submit.v1"), run_id: e().min(1).max(128), request_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), deterministic_session_id: e().min(1).max(256), deterministic_request_id: e().min(1).max(256), workspace_ref: e().min(1).max(2048), prompt_ref: e().min(1).max(2048), agent_preset: s([e().max(256), a()]), permission_ref: e().min(1).max(2048), deadline_at: e().datetime({ offset: !0 }), model_step_timeout_ms: r().int().gte(1e3).lte(6e5), max_tool_calls: r().int().gte(1).lte(100), generation: r().int().gte(1) }).strict();
u({ source_commit: e().regex(new RegExp("^[a-f0-9]{40}$")), source_version: e().min(1).max(128), package_versions: S(e().min(1).max(128)), plugin_build_hash: e().regex(new RegExp("^[a-f0-9]{64}$")) }).strict();
u({ case_id: e().min(1), thesis: e().min(1), main_chain: o(u({ link_id: e().min(1), claim_type: d(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(d(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), opposite_chain: o(u({ link_id: e().min(1), claim_type: d(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(d(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), unresolved_questions: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict();
u({ link_id: e().min(1), claim_type: d(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(d(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict();
u({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: d(["hard", "soft"]), summary: e().min(1), resolution_status: d(["open", "resolved", "bounded_stop"]) }).strict();
u({ status: d(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(u({ requirement_id: e().min(1), importance: d(["hard", "soft"]), reason_code: d(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable", "semantic_mismatch", "no_baseline", "window_missing"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: p() }).strict()), conflicts: o(u({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: d(["hard", "soft"]), summary: e().min(1), resolution_status: d(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict();
u({ provider_id: e().min(1).max(128), error_code: e().min(1).max(128), retryable: p() }).strict();
u({ provider_id: e().min(1).max(128), source_id: e().min(1).max(256), venue: e().min(1).max(128), metric_family: e().min(1).max(128), field: e().min(1).max(128), value: s([r(), e()]), unit: e().min(1).max(64), source_url: s([e().url().max(2048), a()]), published_at: s([e().datetime({ offset: !0 }), a()]) }).strict();
u({ schema_version: h("crypto-event-window-payload.v1"), event_id: e().min(1).max(128), offset: e().min(1).max(32), target_at: e().datetime({ offset: !0 }), captured_at: e().datetime({ offset: !0 }), observations: o(u({ provider_id: e().min(1).max(128), source_id: e().min(1).max(256), venue: e().min(1).max(128), metric_family: e().min(1).max(128), field: e().min(1).max(128), value: s([r(), e()]), unit: e().min(1).max(64), source_url: s([e().url().max(2048), a()]), published_at: s([e().datetime({ offset: !0 }), a()]) }).strict()).max(100), failures: o(u({ provider_id: e().min(1).max(128), error_code: e().min(1).max(128), retryable: p() }).strict()).max(20) }).strict();
u({ schema_version: h("domain-pack-manifest.v1"), pack_id: e().regex(new RegExp("^[a-z][a-z0-9_-]*$")), version: e().min(1), product_extension_ref: e().min(1), doctrine_ref: e().min(1), evidence_policy_ref: e().min(1), gate_policy_ref: e().min(1), evaluation_policy_ref: e().min(1), role_profile_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), capability_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), horizons: o(d(["30m", "24h", "72h"])).min(3).max(3).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), execution_budget: u({ max_evidence_rounds: r().int().gte(1).lte(10), max_tool_calls: r().int().gte(1).lte(100), max_subagents: r().int().gte(0).lte(32), total_deadline_seconds: r().int().gte(1).lte(3600), per_tool_timeout_seconds: r().int().gte(1).lte(600), per_model_step_timeout_seconds: r().int().gte(1).lte(600), max_structured_repairs: r().int().gte(0).lte(5), max_estimated_cost_usd: r().gte(0) }).strict() }).strict();
const Et = u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict();
u({ schema_version: h("event-watch.v1"), watch_id: e().min(1).max(128), event_id: e().min(1).max(128), source_id: e().min(1).max(128), event_family: e().min(1).max(128), scheduled_at: e().datetime({ offset: !0 }), status: d(["scheduled", "active", "completed", "retrospective_only", "cancelled"]), window_offsets: o(e().min(1).max(32)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), baseline_status: d(["pending", "ready", "unavailable"]), created_at: e().datetime({ offset: !0 }), updated_at: e().datetime({ offset: !0 }), next_tick_at: s([e().datetime({ offset: !0 }), a()]) }).strict();
u({ schema_version: h("event-window-capture.v1"), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), provider_id: e().min(1).max(128), payload_ref: e().min(1).max(2048), payload_hash: e().regex(new RegExp("^[a-f0-9]{64}$")) }).strict();
u({ schema_version: h("event-window-sample.v1"), sample_id: e().min(1).max(160), watch_id: e().min(1).max(128), event_id: e().min(1).max(128), offset: e().min(1).max(32), target_at: e().datetime({ offset: !0 }), status: d(["pending", "due", "captured", "missing", "baseline_unavailable"]), observed_at: s([e().datetime({ offset: !0 }), a()]), received_at: s([e().datetime({ offset: !0 }), a()]), provider_id: s([e().max(128), a()]), payload_ref: s([e().max(2048), a()]), payload_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), error_code: s([e().max(128), a()]) }).strict();
u({ evidence_id: e().min(1), requirement_id: e().min(1), kind: d(["official", "market", "web", "transcript", "document"]), authority: d(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3), structured_payload_ref: s([e(), a()]), tool_call_id: s([e(), a()]), research_session_id: e().min(1), round: r().int().gte(1), quality: d(["candidate", "accepted", "rejected", "stale", "conflicted"]), freshness_status: d(["fresh", "stale", "unknown"]), conflict_group: s([e(), a()]) }).strict();
u({ requirement_id: e().min(1), importance: d(["hard", "soft"]), reason_code: d(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable", "semantic_mismatch", "no_baseline", "window_missing"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: p() }).strict();
u({ requirement_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), description: e().min(1), importance: d(["hard", "soft"]), source_priority: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), authority_floor: d(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), preferred_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), freshness_seconds: r().int().gte(0), minimum_independent_sources: r().int().gte(1).lte(10), allowed_fallbacks: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap: r().gte(0).lte(1), accepted_metric_families: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!").default([]), required_metric_families: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!").default([]), required_fields: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!").default([]), required_event_offsets: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!").default([]), unit_policy: s([e(), a()]).default(null), field_units: S(o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!")).default({}), minimum_venues: r().int().gte(1).lte(20).default(1), venue_required: p().default(!1), minimum_independence_groups: r().int().gte(1).lte(20).default(1), allowed_delay_classes: o(d(["realtime", "delayed", "historical", "unknown"])).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!").default([]), semantic_policy_ref: s([e(), a()]).default(null) }).strict();
u({ max_evidence_rounds: r().int().gte(1).lte(10), max_tool_calls: r().int().gte(1).lte(100), max_subagents: r().int().gte(0).lte(32), total_deadline_seconds: r().int().gte(1).lte(3600), per_tool_timeout_seconds: r().int().gte(1).lte(600), per_model_step_timeout_seconds: r().int().gte(1).lte(600), max_structured_repairs: r().int().gte(0).lte(5), max_estimated_cost_usd: r().gte(0) }).strict();
u({ horizon: d(["30m", "24h", "72h"]), action: d(["long", "short", "neutral", "no_trade"]), subjective_probability: r().gte(0).lte(1), probability_status: d(["uncalibrated", "calibrated"]), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), trigger: e().min(1), invalidation: e().min(1), expires_at: e().datetime({ offset: !0 }), next_review_at: e().datetime({ offset: !0 }), missing_facts: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap_reason: s([e(), a()]) }).strict();
u({ schema_version: h("product-extension-manifest.v1"), extension_id: e().regex(new RegExp("^[a-z][a-z0-9_-]*$")), version: e().min(1), task_schema_ref: e().min(1), artifact_schema_ref: e().min(1), evaluation_policy_ref: e().min(1), domain_pack_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), dsh_bundle_ref: s([e(), a()]) }).strict();
u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict();
u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), adapter_ref: e().min(1), route_role: d(["primary", "fallback"]), priority: r().int().gte(0).lte(1e4), service_tier: d(["free_proxy", "licensed_live", "replay"]), allowed_domains: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), timeout_seconds: r().int().gte(1).lte(600), cost_policy_ref: e().min(1), license_status: d(["approved", "review_required", "denied"]), audit_status: d(["approved", "candidate", "denied"]), requires_event_window: p().default(!1) }).strict();
u({ schema_version: h("research-capability-manifest.v1"), capability_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), version: e().min(1), kind: d(["dsh_plugin", "mcp", "provider", "python_adapter", "replay"]), implementation_ref: e().min(1), input_schema_ref: e().min(1), output_schema_ref: e().min(1), permissions: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), allowed_domains: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), timeout_seconds: r().int().gte(1).lte(600), cost_policy_ref: e().min(1), freshness_policy_ref: e().min(1), license_status: d(["approved", "review_required", "denied"]), audit_status: d(["approved", "candidate", "denied"]), replay_policy: d(["archive_required", "deterministic", "unavailable"]), secret_policy: d(["none", "adapter_only", "local_secret_store"]), provider_routes: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), adapter_ref: e().min(1), route_role: d(["primary", "fallback"]), priority: r().int().gte(0).lte(1e4), service_tier: d(["free_proxy", "licensed_live", "replay"]), allowed_domains: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), timeout_seconds: r().int().gte(1).lte(600), cost_policy_ref: e().min(1), license_status: d(["approved", "review_required", "denied"]), audit_status: d(["approved", "candidate", "denied"]), requires_event_window: p().default(!1) }).strict()).max(20).default([]) }).strict();
const St = u({ schema_version: h("research-capability-query.v1"), request_id: e().min(1), capability_id: e().min(1), requirement_id: e().min(1), query: e().min(1).max(2e3), target_url: s([e().url(), a()]), symbols: o(e().min(1)).max(20).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), fields: o(e().min(1)).max(50).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), allowed_domains: o(e().min(1)).max(100).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), max_results: r().int().gte(1).lte(20), max_cost_usd: s([r().gte(0), a()]), research_session_id: e().min(1), round: r().int().gte(1), mode: d(["live", "replay"]), observed_at: e().datetime({ offset: !0 }), requested_observed_at: s([e().datetime({ offset: !0 }), a()]).optional(), cutoff_at: e().datetime({ offset: !0 }), event_id: s([e().max(128), a()]).default(null), event_at: s([e().datetime({ offset: !0 }), a()]).default(null), window_start_at: s([e().datetime({ offset: !0 }), a()]).default(null), window_end_at: s([e().datetime({ offset: !0 }), a()]).default(null), requested_event_offsets: o(e().min(1).max(32)).max(20).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!").default([]) }).strict(), jt = u({ schema_version: h("research-capability-result.v1"), request_id: e().min(1), capability_id: e().min(1), provider: e().min(1), evidence_candidates: o(u({ evidence_id: e().min(1), requirement_id: e().min(1), kind: d(["official", "market", "web", "transcript", "document"]), authority: d(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3), structured_payload_ref: s([e(), a()]), tool_call_id: s([e(), a()]), research_session_id: e().min(1), round: r().int().gte(1), quality: d(["candidate", "accepted", "rejected", "stale", "conflicted"]), freshness_status: d(["fresh", "stale", "unknown"]), conflict_group: s([e(), a()]) }).strict()).max(20), facts: o(u({ schema_version: h("fact-envelope.v1"), fact_id: e().min(1), evidence_id: e().min(1), requirement_id: e().min(1), metric_family: e().min(1), field: e().min(1), instrument: s([e(), a()]), venue: s([e(), a()]), value: s([r(), e(), a()]), unit: e().min(1), window_start_at: s([e().datetime({ offset: !0 }), a()]), window_end_at: s([e().datetime({ offset: !0 }), a()]), event_offset: s([e(), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), published_at: s([e().datetime({ offset: !0 }), a()]), source_id: e().min(1), independence_group: e().min(1), quality: d(["candidate", "accepted", "rejected", "stale", "conflicted"]), delay_class: d(["realtime", "delayed", "historical", "unknown"]), payload_schema_ref: e().min(1), payload_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), attributes: S(s([r(), e(), p(), a()])) }).strict()).max(200).default([]), cost_usd: s([r().gte(0), a()]), completed_at: e().datetime({ offset: !0 }), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict();
u({ capability_id: e().min(1), requirement_id: e().min(1), query_aliases: o(e().min(1).max(2e3)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), evidence_candidates: o(u({ evidence_id: e().min(1), requirement_id: e().min(1), kind: d(["official", "market", "web", "transcript", "document"]), authority: d(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3), structured_payload_ref: s([e(), a()]), tool_call_id: s([e(), a()]), research_session_id: e().min(1), round: r().int().gte(1), quality: d(["candidate", "accepted", "rejected", "stale", "conflicted"]), freshness_status: d(["fresh", "stale", "unknown"]), conflict_group: s([e(), a()]) }).strict()).min(1).max(20) }).strict();
u({ schema_version: h("research-evaluation-case.v1"), case_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), event_family: d(["central_bank_speech", "monetary_policy_decision", "inflation_release", "labor_release", "geopolitical_shock"]), input_text: e().min(1).max(1e4), trigger_evidence: u({ evidence_id: e().min(1), kind: d(["official", "market", "web", "transcript", "document"]), authority: d(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3) }).strict(), evidence_requirements: o(u({ requirement_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), description: e().min(1), importance: d(["hard", "soft"]), source_priority: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), authority_floor: d(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), preferred_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), freshness_seconds: r().int().gte(0), minimum_independent_sources: r().int().gte(1).lte(10), allowed_fallbacks: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap: r().gte(0).lte(1), accepted_metric_families: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!").default([]), required_metric_families: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!").default([]), required_fields: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!").default([]), required_event_offsets: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!").default([]), unit_policy: s([e(), a()]).default(null), field_units: S(o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!")).default({}), minimum_venues: r().int().gte(1).lte(20).default(1), venue_required: p().default(!1), minimum_independence_groups: r().int().gte(1).lte(20).default(1), allowed_delay_classes: o(d(["realtime", "delayed", "historical", "unknown"])).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!").default([]), semantic_policy_ref: s([e(), a()]).default(null) }).strict()).min(1), expected_hard_requirement_ids: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), archived_capability_fixtures: o(u({ capability_id: e().min(1), requirement_id: e().min(1), query_aliases: o(e().min(1).max(2e3)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), evidence_candidates: o(u({ evidence_id: e().min(1), requirement_id: e().min(1), kind: d(["official", "market", "web", "transcript", "document"]), authority: d(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3), structured_payload_ref: s([e(), a()]), tool_call_id: s([e(), a()]), research_session_id: e().min(1), round: r().int().gte(1), quality: d(["candidate", "accepted", "rejected", "stale", "conflicted"]), freshness_status: d(["fresh", "stale", "unknown"]), conflict_group: s([e(), a()]) }).strict()).min(1).max(20) }).strict()).min(1), cutoff_at: e().datetime({ offset: !0 }), outcome_available_at: s([e().datetime({ offset: !0 }), a()]), outcome_labels: o(u({ label_id: e().min(1), value: e().min(1), available_at: e().datetime({ offset: !0 }), source_ref: e().min(1) }).strict()), source_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict();
u({ label_id: e().min(1), value: e().min(1), available_at: e().datetime({ offset: !0 }), source_ref: e().min(1) }).strict();
u({ evidence_id: e().min(1), kind: d(["official", "market", "web", "transcript", "document"]), authority: d(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3) }).strict();
u({ plan_id: e().min(1), objective: e().min(1), tasks: o(u({ task_id: e().min(1), requirement_id: e().min(1), capability_id: e().min(1), objective: e().min(1), question: e().min(1), input_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), output_schema_ref: e().min(1), depends_on: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), success_condition: e().min(1), priority: r().int().gte(1).lte(100) }).strict()).min(1), required_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), created_at: e().datetime({ offset: !0 }) }).strict();
u({ round: r().int().gte(1), plan: u({ plan_id: e().min(1), objective: e().min(1), tasks: o(u({ task_id: e().min(1), requirement_id: e().min(1), capability_id: e().min(1), objective: e().min(1), question: e().min(1), input_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), output_schema_ref: e().min(1), depends_on: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), success_condition: e().min(1), priority: r().int().gte(1).lte(100) }).strict()).min(1), required_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), created_at: e().datetime({ offset: !0 }) }).strict(), tool_invocations: o(u({ tool_call_id: e().min(1), capability_id: e().min(1), tool_name: e().min(1), query_summary: e().min(1).max(1e3), started_at: e().datetime({ offset: !0 }), finished_at: s([e().datetime({ offset: !0 }), a()]), status: d(["running", "succeeded", "failed", "denied", "timed_out"]), attempt: r().int().gte(1), latency_ms: s([r().int().gte(0), a()]), cost_usd: s([r().gte(0), a()]), error_code: s([e(), a()]), error: s([u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict(), a()]).optional() }).strict()), tool_results: o(u({ tool_call_id: e().min(1), status: d(["succeeded", "failed", "denied", "timed_out"]), summary: e().min(1).max(2e3), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), content_ref: s([e(), a()]), content_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), received_at: e().datetime({ offset: !0 }), error_code: s([e(), a()]), error: s([u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict(), a()]).optional() }).strict()), new_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), coverage: u({ status: d(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(u({ requirement_id: e().min(1), importance: d(["hard", "soft"]), reason_code: d(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable", "semantic_mismatch", "no_baseline", "window_missing"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: p() }).strict()), conflicts: o(u({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: d(["hard", "soft"]), summary: e().min(1), resolution_status: d(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }) }).strict();
const $t = u({ schema_version: h("research-run-command.v1"), request_id: e().min(1), command: d(["cancel", "retry", "recheck", "feedback"]), reason: e().min(1) }).strict(), Tt = u({ schema_version: h("research-run-command-result.v1"), request_id: e().min(1), command: d(["cancel", "retry", "recheck", "feedback"]), source_run_id: e().min(1), target_run_id: s([e(), a()]), status: d(["accepted", "already_applied", "rejected"]), created_at: e().datetime({ offset: !0 }) }).strict(), zt = u({ schema_version: h("research-run-detail-view.v1"), run: u({ schema_version: h("research-run-view.v2"), run_id: e().min(1), event_id: e().min(1), event_title: e().min(1), admission_origin: d(["manual", "automatic", "scheduled_recheck", "legacy"]), priority: d(["critical", "high", "normal", "low"]), status: d(["admitted", "queued", "researching", "retry_wait", "completed", "degraded", "research_only", "rejected", "failed", "cancelled"]), stage: d(["admission", "planning", "acquiring_evidence", "assessing_sufficiency", "synthesis", "gate", "monitoring", "done"]), runtime_id: e().min(1), profile_ref: e().min(1), current_round: r().int().gte(0), budget: u({ max_evidence_rounds: r().int().gte(1).lte(10), max_tool_calls: r().int().gte(1).lte(100), max_subagents: r().int().gte(0).lte(32), total_deadline_seconds: r().int().gte(1).lte(3600), per_tool_timeout_seconds: r().int().gte(1).lte(600), per_model_step_timeout_seconds: r().int().gte(1).lte(600), max_structured_repairs: r().int().gte(0).lte(5), max_estimated_cost_usd: r().gte(0) }).strict(), coverage: s([u({ status: d(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(u({ requirement_id: e().min(1), importance: d(["hard", "soft"]), reason_code: d(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable", "semantic_mismatch", "no_baseline", "window_missing"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: p() }).strict()), conflicts: o(u({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: d(["hard", "soft"]), summary: e().min(1), resolution_status: d(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), a()]), current_action: s([e(), a()]), stop_reason: s([u({ code: d(["sufficient", "deadline", "cost_budget", "tool_budget", "round_budget", "permission_denied", "critical_data_unavailable", "runtime_unavailable", "cancelled"]), detail: e().min(1), bounded: p(), remaining_hard_gaps: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), a()]), failure: s([u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict(), a()]).optional(), latest_sequence_no: r().int().gte(0), artifact_id: s([e(), a()]), available_at: s([e().datetime({ offset: !0 }), a()]), parent_run_id: s([e(), a()]), created_at: e().datetime({ offset: !0 }), updated_at: e().datetime({ offset: !0 }) }).strict(), trigger_snapshot: s([u({ schema_version: h("research-snapshot-manifest.v1"), snapshot_id: e().min(1), run_id: e().min(1), event_id: e().min(1), snapshot_type: d(["trigger", "decision"]), generation: r().int().gte(1), parent_snapshot_id: s([e(), a()]), cutoff_at: e().datetime({ offset: !0 }), snapshot_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), pack_version: e().min(1), created_at: e().datetime({ offset: !0 }) }).strict(), a()]), decision_snapshot: s([u({ schema_version: h("research-snapshot-manifest.v1"), snapshot_id: e().min(1), run_id: e().min(1), event_id: e().min(1), snapshot_type: d(["trigger", "decision"]), generation: r().int().gte(1), parent_snapshot_id: s([e(), a()]), cutoff_at: e().datetime({ offset: !0 }), snapshot_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), pack_version: e().min(1), created_at: e().datetime({ offset: !0 }) }).strict(), a()]), evidence: o(u({ evidence_id: e().min(1), requirement_id: e().min(1), kind: d(["official", "market", "web", "transcript", "document"]), authority: d(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3), structured_payload_ref: s([e(), a()]), tool_call_id: s([e(), a()]), research_session_id: e().min(1), round: r().int().gte(1), quality: d(["candidate", "accepted", "rejected", "stale", "conflicted"]), freshness_status: d(["fresh", "stale", "unknown"]), conflict_group: s([e(), a()]) }).strict()), rounds: o(u({ round: r().int().gte(1), plan: u({ plan_id: e().min(1), objective: e().min(1), tasks: o(u({ task_id: e().min(1), requirement_id: e().min(1), capability_id: e().min(1), objective: e().min(1), question: e().min(1), input_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), output_schema_ref: e().min(1), depends_on: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), success_condition: e().min(1), priority: r().int().gte(1).lte(100) }).strict()).min(1), required_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), created_at: e().datetime({ offset: !0 }) }).strict(), tool_invocations: o(u({ tool_call_id: e().min(1), capability_id: e().min(1), tool_name: e().min(1), query_summary: e().min(1).max(1e3), started_at: e().datetime({ offset: !0 }), finished_at: s([e().datetime({ offset: !0 }), a()]), status: d(["running", "succeeded", "failed", "denied", "timed_out"]), attempt: r().int().gte(1), latency_ms: s([r().int().gte(0), a()]), cost_usd: s([r().gte(0), a()]), error_code: s([e(), a()]), error: s([u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict(), a()]).optional() }).strict()), tool_results: o(u({ tool_call_id: e().min(1), status: d(["succeeded", "failed", "denied", "timed_out"]), summary: e().min(1).max(2e3), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), content_ref: s([e(), a()]), content_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), received_at: e().datetime({ offset: !0 }), error_code: s([e(), a()]), error: s([u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict(), a()]).optional() }).strict()), new_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), coverage: u({ status: d(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(u({ requirement_id: e().min(1), importance: d(["hard", "soft"]), reason_code: d(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable", "semantic_mismatch", "no_baseline", "window_missing"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: p() }).strict()), conflicts: o(u({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: d(["hard", "soft"]), summary: e().min(1), resolution_status: d(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }) }).strict()), causal_case: s([u({ case_id: e().min(1), thesis: e().min(1), main_chain: o(u({ link_id: e().min(1), claim_type: d(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(d(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), opposite_chain: o(u({ link_id: e().min(1), claim_type: d(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(d(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), unresolved_questions: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), a()]), horizons: o(u({ horizon: d(["30m", "24h", "72h"]), action: d(["long", "short", "neutral", "no_trade"]), subjective_probability: r().gte(0).lte(1), probability_status: d(["uncalibrated", "calibrated"]), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), trigger: e().min(1), invalidation: e().min(1), expires_at: e().datetime({ offset: !0 }), next_review_at: e().datetime({ offset: !0 }), missing_facts: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap_reason: s([e(), a()]) }).strict()).max(3), trace: o(u({ schema_version: h("research-trace-event.v1"), run_id: e().min(1), research_session_id: e().min(1), sequence_no: r().int().gte(0), event_type: d(["session_started", "plan_created", "task_started", "model_step_started", "model_step_completed", "tool_started", "tool_completed", "tool_failed", "subagent_started", "subagent_completed", "evidence_accepted", "evidence_rejected", "coverage_assessed", "replan", "round_completed", "synthesis_started", "session_stopped"]), occurred_at: e().datetime({ offset: !0 }), stage: e().min(1), summary: e().min(1).max(2e3), reference_type: s([e(), a()]), reference_id: s([e(), a()]), status: d(["running", "succeeded", "failed", "degraded", "denied"]), error_code: s([e(), a()]), error: s([u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict(), a()]).optional() }).strict()), total_tool_calls: r().int().gte(0), total_subagents: r().int().gte(0), total_tokens: s([r().int().gte(0), a()]), estimated_cost_usd: s([r().gte(0), a()]), scheduled_recheck_at: s([e().datetime({ offset: !0 }), a()]) }).strict(), Ct = u({ schema_version: h("research-run-queued.v1"), event_id: e().min(1).max(128), run_id: e().min(1).max(128), status: h("queued"), status_url: e().min(1).max(2048) }).strict();
u({ schema_version: h("research-run-view.v2"), run_id: e().min(1), event_id: e().min(1), event_title: e().min(1), admission_origin: d(["manual", "automatic", "scheduled_recheck", "legacy"]), priority: d(["critical", "high", "normal", "low"]), status: d(["admitted", "queued", "researching", "retry_wait", "completed", "degraded", "research_only", "rejected", "failed", "cancelled"]), stage: d(["admission", "planning", "acquiring_evidence", "assessing_sufficiency", "synthesis", "gate", "monitoring", "done"]), runtime_id: e().min(1), profile_ref: e().min(1), current_round: r().int().gte(0), budget: u({ max_evidence_rounds: r().int().gte(1).lte(10), max_tool_calls: r().int().gte(1).lte(100), max_subagents: r().int().gte(0).lte(32), total_deadline_seconds: r().int().gte(1).lte(3600), per_tool_timeout_seconds: r().int().gte(1).lte(600), per_model_step_timeout_seconds: r().int().gte(1).lte(600), max_structured_repairs: r().int().gte(0).lte(5), max_estimated_cost_usd: r().gte(0) }).strict(), coverage: s([u({ status: d(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(u({ requirement_id: e().min(1), importance: d(["hard", "soft"]), reason_code: d(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable", "semantic_mismatch", "no_baseline", "window_missing"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: p() }).strict()), conflicts: o(u({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: d(["hard", "soft"]), summary: e().min(1), resolution_status: d(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), a()]), current_action: s([e(), a()]), stop_reason: s([u({ code: d(["sufficient", "deadline", "cost_budget", "tool_budget", "round_budget", "permission_denied", "critical_data_unavailable", "runtime_unavailable", "cancelled"]), detail: e().min(1), bounded: p(), remaining_hard_gaps: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), a()]), failure: s([u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict(), a()]).optional(), latest_sequence_no: r().int().gte(0), artifact_id: s([e(), a()]), available_at: s([e().datetime({ offset: !0 }), a()]), parent_run_id: s([e(), a()]), created_at: e().datetime({ offset: !0 }), updated_at: e().datetime({ offset: !0 }) }).strict();
u({ schema_version: h("research-runtime-case-report.v1"), dataset_id: e().min(1), case_id: e().min(1), runtime_id: e().min(1), runtime_version: e().min(1), status: d(["completed", "degraded", "failed", "timeout"]), cutoff_at: e().datetime({ offset: !0 }), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: s([r().int().gte(0), a()]), result_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), result: s([u({ schema_version: h("research-session-result.v1"), request_id: e().min(1), research_session_id: e().min(1), runtime_id: e().min(1), runtime_version: e().min(1), profile_ref: e().min(1), trace_ref: e().min(1), trace_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), status: d(["completed", "degraded", "failed", "cancelled"]), rounds: o(u({ round: r().int().gte(1), plan: u({ plan_id: e().min(1), objective: e().min(1), tasks: o(u({ task_id: e().min(1), requirement_id: e().min(1), capability_id: e().min(1), objective: e().min(1), question: e().min(1), input_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), output_schema_ref: e().min(1), depends_on: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), success_condition: e().min(1), priority: r().int().gte(1).lte(100) }).strict()).min(1), required_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), created_at: e().datetime({ offset: !0 }) }).strict(), tool_invocations: o(u({ tool_call_id: e().min(1), capability_id: e().min(1), tool_name: e().min(1), query_summary: e().min(1).max(1e3), started_at: e().datetime({ offset: !0 }), finished_at: s([e().datetime({ offset: !0 }), a()]), status: d(["running", "succeeded", "failed", "denied", "timed_out"]), attempt: r().int().gte(1), latency_ms: s([r().int().gte(0), a()]), cost_usd: s([r().gte(0), a()]), error_code: s([e(), a()]), error: s([u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict(), a()]).optional() }).strict()), tool_results: o(u({ tool_call_id: e().min(1), status: d(["succeeded", "failed", "denied", "timed_out"]), summary: e().min(1).max(2e3), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), content_ref: s([e(), a()]), content_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), received_at: e().datetime({ offset: !0 }), error_code: s([e(), a()]), error: s([u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict(), a()]).optional() }).strict()), new_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), coverage: u({ status: d(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(u({ requirement_id: e().min(1), importance: d(["hard", "soft"]), reason_code: d(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable", "semantic_mismatch", "no_baseline", "window_missing"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: p() }).strict()), conflicts: o(u({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: d(["hard", "soft"]), summary: e().min(1), resolution_status: d(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }) }).strict()).min(1), evidence_candidates: o(u({ evidence_id: e().min(1), requirement_id: e().min(1), kind: d(["official", "market", "web", "transcript", "document"]), authority: d(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3), structured_payload_ref: s([e(), a()]), tool_call_id: s([e(), a()]), research_session_id: e().min(1), round: r().int().gte(1), quality: d(["candidate", "accepted", "rejected", "stale", "conflicted"]), freshness_status: d(["fresh", "stale", "unknown"]), conflict_group: s([e(), a()]) }).strict()), facts: o(u({ schema_version: h("fact-envelope.v1"), fact_id: e().min(1), evidence_id: e().min(1), requirement_id: e().min(1), metric_family: e().min(1), field: e().min(1), instrument: s([e(), a()]), venue: s([e(), a()]), value: s([r(), e(), a()]), unit: e().min(1), window_start_at: s([e().datetime({ offset: !0 }), a()]), window_end_at: s([e().datetime({ offset: !0 }), a()]), event_offset: s([e(), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), published_at: s([e().datetime({ offset: !0 }), a()]), source_id: e().min(1), independence_group: e().min(1), quality: d(["candidate", "accepted", "rejected", "stale", "conflicted"]), delay_class: d(["realtime", "delayed", "historical", "unknown"]), payload_schema_ref: e().min(1), payload_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), attributes: S(s([r(), e(), p(), a()])) }).strict()).max(1e3).default([]), final_coverage: u({ status: d(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(u({ requirement_id: e().min(1), importance: d(["hard", "soft"]), reason_code: d(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable", "semantic_mismatch", "no_baseline", "window_missing"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: p() }).strict()), conflicts: o(u({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: d(["hard", "soft"]), summary: e().min(1), resolution_status: d(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), causal_case: s([u({ case_id: e().min(1), thesis: e().min(1), main_chain: o(u({ link_id: e().min(1), claim_type: d(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(d(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), opposite_chain: o(u({ link_id: e().min(1), claim_type: d(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(d(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), unresolved_questions: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), a()]), horizons: o(u({ horizon: d(["30m", "24h", "72h"]), action: d(["long", "short", "neutral", "no_trade"]), subjective_probability: r().gte(0).lte(1), probability_status: d(["uncalibrated", "calibrated"]), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), trigger: e().min(1), invalidation: e().min(1), expires_at: e().datetime({ offset: !0 }), next_review_at: e().datetime({ offset: !0 }), missing_facts: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap_reason: s([e(), a()]) }).strict()).max(3), stop_reason: u({ code: d(["sufficient", "deadline", "cost_budget", "tool_budget", "round_budget", "permission_denied", "critical_data_unavailable", "runtime_unavailable", "cancelled"]), detail: e().min(1), bounded: p(), remaining_hard_gaps: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), synthesis_failure_code: s([e().max(128), a()]).optional(), total_tool_calls: r().int().gte(0), total_subagents: r().int().gte(0), total_tokens: s([r().int().gte(0), a()]), estimated_cost_usd: s([r().gte(0), a()]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }) }).strict(), a()]), hard_coverage_ratio: r().gte(0).lte(1), hard_gap_count: r().int().gte(0), evidence_count: r().int().gte(0), unattested_evidence_count: r().int().gte(0), pit_violations: r().int().gte(0), horizon_count: r().int().gte(0).lte(3), horizon_distinct: p(), tool_calls: r().int().gte(0), subagents: r().int().gte(0), total_tokens: s([r().int().gte(0), a()]), estimated_cost_usd: s([r().gte(0), a()]), failure_code: s([e(), a()]) }).strict();
u({ schema_version: h("research-runtime-comparison.v1"), experiment_id: e().min(1), dataset_id: e().min(1), dataset_manifest_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), baseline_runtime_id: e().min(1), candidate_runtime_id: e().min(1), case_reports: o(u({ schema_version: h("research-runtime-case-report.v1"), dataset_id: e().min(1), case_id: e().min(1), runtime_id: e().min(1), runtime_version: e().min(1), status: d(["completed", "degraded", "failed", "timeout"]), cutoff_at: e().datetime({ offset: !0 }), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: s([r().int().gte(0), a()]), result_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), result: s([u({ schema_version: h("research-session-result.v1"), request_id: e().min(1), research_session_id: e().min(1), runtime_id: e().min(1), runtime_version: e().min(1), profile_ref: e().min(1), trace_ref: e().min(1), trace_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), status: d(["completed", "degraded", "failed", "cancelled"]), rounds: o(u({ round: r().int().gte(1), plan: u({ plan_id: e().min(1), objective: e().min(1), tasks: o(u({ task_id: e().min(1), requirement_id: e().min(1), capability_id: e().min(1), objective: e().min(1), question: e().min(1), input_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), output_schema_ref: e().min(1), depends_on: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), success_condition: e().min(1), priority: r().int().gte(1).lte(100) }).strict()).min(1), required_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), created_at: e().datetime({ offset: !0 }) }).strict(), tool_invocations: o(u({ tool_call_id: e().min(1), capability_id: e().min(1), tool_name: e().min(1), query_summary: e().min(1).max(1e3), started_at: e().datetime({ offset: !0 }), finished_at: s([e().datetime({ offset: !0 }), a()]), status: d(["running", "succeeded", "failed", "denied", "timed_out"]), attempt: r().int().gte(1), latency_ms: s([r().int().gte(0), a()]), cost_usd: s([r().gte(0), a()]), error_code: s([e(), a()]), error: s([u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict(), a()]).optional() }).strict()), tool_results: o(u({ tool_call_id: e().min(1), status: d(["succeeded", "failed", "denied", "timed_out"]), summary: e().min(1).max(2e3), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), content_ref: s([e(), a()]), content_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), received_at: e().datetime({ offset: !0 }), error_code: s([e(), a()]), error: s([u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict(), a()]).optional() }).strict()), new_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), coverage: u({ status: d(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(u({ requirement_id: e().min(1), importance: d(["hard", "soft"]), reason_code: d(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable", "semantic_mismatch", "no_baseline", "window_missing"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: p() }).strict()), conflicts: o(u({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: d(["hard", "soft"]), summary: e().min(1), resolution_status: d(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }) }).strict()).min(1), evidence_candidates: o(u({ evidence_id: e().min(1), requirement_id: e().min(1), kind: d(["official", "market", "web", "transcript", "document"]), authority: d(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3), structured_payload_ref: s([e(), a()]), tool_call_id: s([e(), a()]), research_session_id: e().min(1), round: r().int().gte(1), quality: d(["candidate", "accepted", "rejected", "stale", "conflicted"]), freshness_status: d(["fresh", "stale", "unknown"]), conflict_group: s([e(), a()]) }).strict()), facts: o(u({ schema_version: h("fact-envelope.v1"), fact_id: e().min(1), evidence_id: e().min(1), requirement_id: e().min(1), metric_family: e().min(1), field: e().min(1), instrument: s([e(), a()]), venue: s([e(), a()]), value: s([r(), e(), a()]), unit: e().min(1), window_start_at: s([e().datetime({ offset: !0 }), a()]), window_end_at: s([e().datetime({ offset: !0 }), a()]), event_offset: s([e(), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), published_at: s([e().datetime({ offset: !0 }), a()]), source_id: e().min(1), independence_group: e().min(1), quality: d(["candidate", "accepted", "rejected", "stale", "conflicted"]), delay_class: d(["realtime", "delayed", "historical", "unknown"]), payload_schema_ref: e().min(1), payload_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), attributes: S(s([r(), e(), p(), a()])) }).strict()).max(1e3).default([]), final_coverage: u({ status: d(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(u({ requirement_id: e().min(1), importance: d(["hard", "soft"]), reason_code: d(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable", "semantic_mismatch", "no_baseline", "window_missing"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: p() }).strict()), conflicts: o(u({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: d(["hard", "soft"]), summary: e().min(1), resolution_status: d(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), causal_case: s([u({ case_id: e().min(1), thesis: e().min(1), main_chain: o(u({ link_id: e().min(1), claim_type: d(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(d(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), opposite_chain: o(u({ link_id: e().min(1), claim_type: d(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(d(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), unresolved_questions: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), a()]), horizons: o(u({ horizon: d(["30m", "24h", "72h"]), action: d(["long", "short", "neutral", "no_trade"]), subjective_probability: r().gte(0).lte(1), probability_status: d(["uncalibrated", "calibrated"]), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), trigger: e().min(1), invalidation: e().min(1), expires_at: e().datetime({ offset: !0 }), next_review_at: e().datetime({ offset: !0 }), missing_facts: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap_reason: s([e(), a()]) }).strict()).max(3), stop_reason: u({ code: d(["sufficient", "deadline", "cost_budget", "tool_budget", "round_budget", "permission_denied", "critical_data_unavailable", "runtime_unavailable", "cancelled"]), detail: e().min(1), bounded: p(), remaining_hard_gaps: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), synthesis_failure_code: s([e().max(128), a()]).optional(), total_tool_calls: r().int().gte(0), total_subagents: r().int().gte(0), total_tokens: s([r().int().gte(0), a()]), estimated_cost_usd: s([r().gte(0), a()]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }) }).strict(), a()]), hard_coverage_ratio: r().gte(0).lte(1), hard_gap_count: r().int().gte(0), evidence_count: r().int().gte(0), unattested_evidence_count: r().int().gte(0), pit_violations: r().int().gte(0), horizon_count: r().int().gte(0).lte(3), horizon_distinct: p(), tool_calls: r().int().gte(0), subagents: r().int().gte(0), total_tokens: s([r().int().gte(0), a()]), estimated_cost_usd: s([r().gte(0), a()]), failure_code: s([e(), a()]) }).strict()).min(1), summaries: o(u({ runtime_id: e().min(1), runtime_version: e().min(1), sample_count: r().int().gte(0), completed_count: r().int().gte(0), failed_count: r().int().gte(0), hard_coverage_mean: r().gte(0).lte(1), evidence_count: r().int().gte(0), unattested_evidence_count: r().int().gte(0), pit_violations: r().int().gte(0), horizon_distinct_count: r().int().gte(0), total_tool_calls: r().int().gte(0), total_subagents: r().int().gte(0), total_tokens: s([r().int().gte(0), a()]), estimated_cost_usd: s([r().gte(0), a()]), p95_latency_ms: s([r().int().gte(0), a()]), failure_counts: S(r().int().gte(1)) }).strict()).min(2).max(2), conclusion: d(["pending", "promotion_recommended", "retain_baseline"]) }).strict();
u({ runtime_id: e().min(1), runtime_version: e().min(1), sample_count: r().int().gte(0), completed_count: r().int().gte(0), failed_count: r().int().gte(0), hard_coverage_mean: r().gte(0).lte(1), evidence_count: r().int().gte(0), unattested_evidence_count: r().int().gte(0), pit_violations: r().int().gte(0), horizon_distinct_count: r().int().gte(0), total_tool_calls: r().int().gte(0), total_subagents: r().int().gte(0), total_tokens: s([r().int().gte(0), a()]), estimated_cost_usd: s([r().gte(0), a()]), p95_latency_ms: s([r().int().gte(0), a()]), failure_counts: S(r().int().gte(1)) }).strict();
u({ schema_version: h("research-session-request.v1"), request_id: e().min(1), run_id: e().min(1), event_id: e().min(1), trigger_snapshot_id: e().min(1), domain_pack_ref: e().min(1), role_profile_ref: e().min(1), execution_mode: d(["live", "replay"]), pit_cutoff_at: e().datetime({ offset: !0 }), current_round: r().int().gte(1).lte(10), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), input_evidence: o(u({ evidence_id: e().min(1), kind: d(["official", "market", "web", "transcript", "document"]), authority: d(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3) }).strict()).min(1).max(50), evidence_requirements: o(u({ requirement_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), description: e().min(1), importance: d(["hard", "soft"]), source_priority: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), authority_floor: d(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), preferred_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), freshness_seconds: r().int().gte(0), minimum_independent_sources: r().int().gte(1).lte(10), allowed_fallbacks: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap: r().gte(0).lte(1), accepted_metric_families: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!").default([]), required_metric_families: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!").default([]), required_fields: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!").default([]), required_event_offsets: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!").default([]), unit_policy: s([e(), a()]).default(null), field_units: S(o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!")).default({}), minimum_venues: r().int().gte(1).lte(20).default(1), venue_required: p().default(!1), minimum_independence_groups: r().int().gte(1).lte(20).default(1), allowed_delay_classes: o(d(["realtime", "delayed", "historical", "unknown"])).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!").default([]), semantic_policy_ref: s([e(), a()]).default(null) }).strict()).min(1), target_gaps: o(u({ requirement_id: e().min(1), importance: d(["hard", "soft"]), reason_code: d(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable", "semantic_mismatch", "no_baseline", "window_missing"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: p() }).strict()).max(50), event_watch: s([u({ schema_version: h("event-watch.v1"), watch_id: e().min(1).max(128), event_id: e().min(1).max(128), source_id: e().min(1).max(128), event_family: e().min(1).max(128), scheduled_at: e().datetime({ offset: !0 }), status: d(["scheduled", "active", "completed", "retrospective_only", "cancelled"]), window_offsets: o(e().min(1).max(32)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), baseline_status: d(["pending", "ready", "unavailable"]), created_at: e().datetime({ offset: !0 }), updated_at: e().datetime({ offset: !0 }), next_tick_at: s([e().datetime({ offset: !0 }), a()]) }).strict(), a()]).default(null), event_window_samples: o(u({ schema_version: h("event-window-sample.v1"), sample_id: e().min(1).max(160), watch_id: e().min(1).max(128), event_id: e().min(1).max(128), offset: e().min(1).max(32), target_at: e().datetime({ offset: !0 }), status: d(["pending", "due", "captured", "missing", "baseline_unavailable"]), observed_at: s([e().datetime({ offset: !0 }), a()]), received_at: s([e().datetime({ offset: !0 }), a()]), provider_id: s([e().max(128), a()]), payload_ref: s([e().max(2048), a()]), payload_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), error_code: s([e().max(128), a()]) }).strict()).max(32).default([]), allowed_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), execution_budget: u({ max_evidence_rounds: r().int().gte(1).lte(10), max_tool_calls: r().int().gte(1).lte(100), max_subagents: r().int().gte(0).lte(32), total_deadline_seconds: r().int().gte(1).lte(3600), per_tool_timeout_seconds: r().int().gte(1).lte(600), per_model_step_timeout_seconds: r().int().gte(1).lte(600), max_structured_repairs: r().int().gte(0).lte(5), max_estimated_cost_usd: r().gte(0) }).strict(), deadline_at: e().datetime({ offset: !0 }), output_schema_ref: e().min(1), repair_instructions: s([e().max(4e3), a()]) }).strict();
u({ schema_version: h("research-session-result.v1"), request_id: e().min(1), research_session_id: e().min(1), runtime_id: e().min(1), runtime_version: e().min(1), profile_ref: e().min(1), trace_ref: e().min(1), trace_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), status: d(["completed", "degraded", "failed", "cancelled"]), rounds: o(u({ round: r().int().gte(1), plan: u({ plan_id: e().min(1), objective: e().min(1), tasks: o(u({ task_id: e().min(1), requirement_id: e().min(1), capability_id: e().min(1), objective: e().min(1), question: e().min(1), input_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), output_schema_ref: e().min(1), depends_on: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), success_condition: e().min(1), priority: r().int().gte(1).lte(100) }).strict()).min(1), required_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), created_at: e().datetime({ offset: !0 }) }).strict(), tool_invocations: o(u({ tool_call_id: e().min(1), capability_id: e().min(1), tool_name: e().min(1), query_summary: e().min(1).max(1e3), started_at: e().datetime({ offset: !0 }), finished_at: s([e().datetime({ offset: !0 }), a()]), status: d(["running", "succeeded", "failed", "denied", "timed_out"]), attempt: r().int().gte(1), latency_ms: s([r().int().gte(0), a()]), cost_usd: s([r().gte(0), a()]), error_code: s([e(), a()]), error: s([u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict(), a()]).optional() }).strict()), tool_results: o(u({ tool_call_id: e().min(1), status: d(["succeeded", "failed", "denied", "timed_out"]), summary: e().min(1).max(2e3), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), content_ref: s([e(), a()]), content_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), received_at: e().datetime({ offset: !0 }), error_code: s([e(), a()]), error: s([u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict(), a()]).optional() }).strict()), new_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), coverage: u({ status: d(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(u({ requirement_id: e().min(1), importance: d(["hard", "soft"]), reason_code: d(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable", "semantic_mismatch", "no_baseline", "window_missing"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: p() }).strict()), conflicts: o(u({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: d(["hard", "soft"]), summary: e().min(1), resolution_status: d(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }) }).strict()).min(1), evidence_candidates: o(u({ evidence_id: e().min(1), requirement_id: e().min(1), kind: d(["official", "market", "web", "transcript", "document"]), authority: d(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), source_id: e().min(1), source_url: s([e().url(), a()]), published_at: s([e().datetime({ offset: !0 }), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), excerpt: e().min(1).max(4e3), structured_payload_ref: s([e(), a()]), tool_call_id: s([e(), a()]), research_session_id: e().min(1), round: r().int().gte(1), quality: d(["candidate", "accepted", "rejected", "stale", "conflicted"]), freshness_status: d(["fresh", "stale", "unknown"]), conflict_group: s([e(), a()]) }).strict()), facts: o(u({ schema_version: h("fact-envelope.v1"), fact_id: e().min(1), evidence_id: e().min(1), requirement_id: e().min(1), metric_family: e().min(1), field: e().min(1), instrument: s([e(), a()]), venue: s([e(), a()]), value: s([r(), e(), a()]), unit: e().min(1), window_start_at: s([e().datetime({ offset: !0 }), a()]), window_end_at: s([e().datetime({ offset: !0 }), a()]), event_offset: s([e(), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), published_at: s([e().datetime({ offset: !0 }), a()]), source_id: e().min(1), independence_group: e().min(1), quality: d(["candidate", "accepted", "rejected", "stale", "conflicted"]), delay_class: d(["realtime", "delayed", "historical", "unknown"]), payload_schema_ref: e().min(1), payload_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), attributes: S(s([r(), e(), p(), a()])) }).strict()).max(1e3).default([]), final_coverage: u({ status: d(["insufficient", "sufficient", "bounded_stop"]), covered_requirement_ids: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), gaps: o(u({ requirement_id: e().min(1), importance: d(["hard", "soft"]), reason_code: d(["missing", "stale", "low_authority", "insufficient_sources", "conflict", "tool_unavailable", "semantic_mismatch", "no_baseline", "window_missing"]), query_hint: e().min(1), attempted_capabilities: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), blocks_directional_output: p() }).strict()), conflicts: o(u({ conflict_id: e().min(1), requirement_id: e().min(1), evidence_refs: o(e().min(1)).min(2).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), severity: d(["hard", "soft"]), summary: e().min(1), resolution_status: d(["open", "resolved", "bounded_stop"]) }).strict()), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), assessed_at: e().datetime({ offset: !0 }) }).strict(), causal_case: s([u({ case_id: e().min(1), thesis: e().min(1), main_chain: o(u({ link_id: e().min(1), claim_type: d(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(d(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), opposite_chain: o(u({ link_id: e().min(1), claim_type: d(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(d(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), unresolved_questions: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), a()]), horizons: o(u({ horizon: d(["30m", "24h", "72h"]), action: d(["long", "short", "neutral", "no_trade"]), subjective_probability: r().gte(0).lte(1), probability_status: d(["uncalibrated", "calibrated"]), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), trigger: e().min(1), invalidation: e().min(1), expires_at: e().datetime({ offset: !0 }), next_review_at: e().datetime({ offset: !0 }), missing_facts: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap_reason: s([e(), a()]) }).strict()).max(3), stop_reason: u({ code: d(["sufficient", "deadline", "cost_budget", "tool_budget", "round_budget", "permission_denied", "critical_data_unavailable", "runtime_unavailable", "cancelled"]), detail: e().min(1), bounded: p(), remaining_hard_gaps: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), synthesis_failure_code: s([e().max(128), a()]).optional(), total_tool_calls: r().int().gte(0), total_subagents: r().int().gte(0), total_tokens: s([r().int().gte(0), a()]), estimated_cost_usd: s([r().gte(0), a()]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }) }).strict();
u({ schema_version: h("research-snapshot-manifest.v1"), snapshot_id: e().min(1), run_id: e().min(1), event_id: e().min(1), snapshot_type: d(["trigger", "decision"]), generation: r().int().gte(1), parent_snapshot_id: s([e(), a()]), cutoff_at: e().datetime({ offset: !0 }), snapshot_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), pack_version: e().min(1), created_at: e().datetime({ offset: !0 }) }).strict();
u({ source_ref: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), tier: d(["P0", "P1", "P2", "P3", "P4"]), publisher: e().min(1), authority: d(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), independence_group: e().min(1), domains: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), allowed_paths: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), requirement_ids: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), allow_search: p(), allow_fetch: p(), allow_evidence: p(), parser_ref: s([e(), a()]), license_status: d(["approved", "review_required", "denied"]), retention_policy: d(["hash_only", "hash_excerpt", "full_text"]), audit_status: d(["approved", "candidate", "denied"]), redirect_policy: d(["deny", "same_source_only"]) }).strict();
u({ schema_version: h("research-source-registry.v1"), pack_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), version: e().min(1), sources: o(u({ source_ref: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), tier: d(["P0", "P1", "P2", "P3", "P4"]), publisher: e().min(1), authority: d(["official", "exchange", "audited_aggregator", "verified_web", "search_derived", "unverified"]), independence_group: e().min(1), domains: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), allowed_paths: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), requirement_ids: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), allow_search: p(), allow_fetch: p(), allow_evidence: p(), parser_ref: s([e(), a()]), license_status: d(["approved", "review_required", "denied"]), retention_policy: d(["hash_only", "hash_excerpt", "full_text"]), audit_status: d(["approved", "candidate", "denied"]), redirect_policy: d(["deny", "same_source_only"]) }).strict()).min(1).max(500) }).strict();
u({ code: d(["sufficient", "deadline", "cost_budget", "tool_budget", "round_budget", "permission_denied", "critical_data_unavailable", "runtime_unavailable", "cancelled"]), detail: e().min(1), bounded: p(), remaining_hard_gaps: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict();
const Nt = u({ schema_version: h("research-synthesis-candidate.v1"), request_id: e().min(1), causal_case: s([u({ case_id: e().min(1), thesis: e().min(1), main_chain: o(u({ link_id: e().min(1), claim_type: d(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(d(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), opposite_chain: o(u({ link_id: e().min(1), claim_type: d(["fact", "inference", "scenario"]), statement: e().min(1), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confirmation: e().min(1), invalidation: e().min(1), affected_horizons: o(d(["30m", "24h", "72h"])).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict()).min(1), unresolved_questions: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!") }).strict(), a()]), horizons: o(u({ horizon: d(["30m", "24h", "72h"]), action: d(["long", "short", "neutral", "no_trade"]), subjective_probability: r().gte(0).lte(1), probability_status: d(["uncalibrated", "calibrated"]), evidence_refs: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), trigger: e().min(1), invalidation: e().min(1), expires_at: e().datetime({ offset: !0 }), next_review_at: e().datetime({ offset: !0 }), missing_facts: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), confidence_cap_reason: s([e(), a()]) }).strict()).max(3) }).strict();
u({ task_id: e().min(1), requirement_id: e().min(1), capability_id: e().min(1), objective: e().min(1), question: e().min(1), input_evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), output_schema_ref: e().min(1), depends_on: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), success_condition: e().min(1), priority: r().int().gte(1).lte(100) }).strict();
u({ schema_version: h("research-trace-event.v1"), run_id: e().min(1), research_session_id: e().min(1), sequence_no: r().int().gte(0), event_type: d(["session_started", "plan_created", "task_started", "model_step_started", "model_step_completed", "tool_started", "tool_completed", "tool_failed", "subagent_started", "subagent_completed", "evidence_accepted", "evidence_rejected", "coverage_assessed", "replan", "round_completed", "synthesis_started", "session_stopped"]), occurred_at: e().datetime({ offset: !0 }), stage: e().min(1), summary: e().min(1).max(2e3), reference_type: s([e(), a()]), reference_id: s([e(), a()]), status: d(["running", "succeeded", "failed", "degraded", "denied"]), error_code: s([e(), a()]), error: s([u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict(), a()]).optional() }).strict();
u({ schema_version: h("role-profile.v1"), profile_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), version: e().min(1), objective: e().min(1), required_capabilities: o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), allowed_tools: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), output_schema_ref: e().min(1), evaluation_policy_ref: e().min(1), system_instruction_ref: e().min(1) }).strict();
u({ tool_call_id: e().min(1), capability_id: e().min(1), tool_name: e().min(1), query_summary: e().min(1).max(1e3), started_at: e().datetime({ offset: !0 }), finished_at: s([e().datetime({ offset: !0 }), a()]), status: d(["running", "succeeded", "failed", "denied", "timed_out"]), attempt: r().int().gte(1), latency_ms: s([r().int().gte(0), a()]), cost_usd: s([r().gte(0), a()]), error_code: s([e(), a()]), error: s([u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict(), a()]).optional() }).strict();
u({ tool_call_id: e().min(1), status: d(["succeeded", "failed", "denied", "timed_out"]), summary: e().min(1).max(2e3), evidence_refs: o(e().min(1)).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!"), content_ref: s([e(), a()]), content_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]), received_at: e().datetime({ offset: !0 }), error_code: s([e(), a()]), error: s([u({ error_code: e().min(1), origin: d(["provider", "transport", "mcp", "dsh", "gateway", "pit", "orchestration"]), cause_code: s([e(), a()]), capability_id: s([e(), a()]), tool_call_id: s([e(), a()]), retryable: p(), deadline_ms: s([r().int().gte(0), a()]), provider_attempts: o(u({ provider_id: e().regex(new RegExp("^[a-z][a-z0-9_.-]*$")), route_role: d(["primary", "fallback"]), service_tier: d(["free_proxy", "licensed_live", "replay"]), status: d(["succeeded", "failed", "skipped"]), started_at: e().datetime({ offset: !0 }), finished_at: e().datetime({ offset: !0 }), latency_ms: r().int().gte(0), cost_usd: s([r().gte(0), a()]), error_code: s([e().max(128), a()]), retryable: s([p(), a()]) }).strict()).max(20).default([]) }).strict(), a()]).optional() }).strict();
o(e().min(1)).min(1).refine((i) => i.every((t, n) => i.indexOf(t) == n), "All items must be unique!");
u({ proposal_id: e().min(1), candidate_type: d(["strategy", "runtime", "profile", "doctrine", "provider_policy"]), version: e().min(1), parent_version: s([e(), a()]), summary: e().min(1).max(1e4), changes: o(e().min(1).max(5e3)).min(1), rationale: e().min(1).max(1e4), evidence_refs: o(e().min(1)) }).strict();
u({ capability_id: e().min(1), capability_type: e().min(1), status: e().min(1), permissions: o(e()), network_domains: o(e()), timeout_seconds: r().gt(0), max_cost_usd: s([r().gte(0), a()]) }).strict();
u({ job_id: e().min(1), schema_version: h("evolution-job.v1"), trigger_key: e().min(1).max(512), trigger_type: d(["scheduled", "feedback", "failure_pattern", "evaluation_batch"]), domain_pack_ref: e().min(1), status: d(["queued", "running", "retry_wait", "pending_owner_review", "completed", "failed", "cancelled"]), stage: d(["discover", "plan", "candidate", "replay", "holdout", "shadow", "review"]), input_refs: o(e().min(1)).min(1), candidate_id: s([e(), a()]), experiment_refs: o(e().min(1)), result_refs: o(e().min(1)), attempt: r().int().gte(0), max_attempts: r().int().gte(1).lte(10), lease_owner: s([e(), a()]), lease_expires_at: s([e().datetime({ offset: !0 }), a()]), next_attempt_at: s([e().datetime({ offset: !0 }), a()]), last_error_code: s([e(), a()]), created_at: e().datetime({ offset: !0 }), updated_at: e().datetime({ offset: !0 }), finished_at: s([e().datetime({ offset: !0 }), a()]) }).strict();
u({ queued: r().int().gte(0), running: r().int().gte(0), retry_wait: r().int().gte(0), pending_owner_review: r().int().gte(0), completed: r().int().gte(0), failed: r().int().gte(0), cancelled: r().int().gte(0) }).strict();
u({ trigger_key: e().min(1).max(512), trigger_type: d(["scheduled", "feedback", "failure_pattern", "evaluation_batch"]), domain_pack_ref: e().min(1), input_refs: o(e().min(1)).min(1), max_attempts: r().int().gte(1).lte(10) }).strict();
u({ job_id: e().min(1), domain_pack_ref: e().min(1), context_items: o(e().min(1)).min(1), evidence_refs: o(e().min(1)), active_candidate_id: s([e(), a()]), active_candidate_version: s([e(), a()]), active_candidate_content_hash: s([e().regex(new RegExp("^[a-f0-9]{64}$")), a()]) }).strict();
u({ schema_version: h("operations-overview.v1"), checked_at: e().datetime({ offset: !0 }), services: o(u({ service_id: e().min(1), role: d(["api", "realtime_worker", "research_worker", "evolution_worker"]), instance_id: s([e().min(1), a()]), version: s([e().min(1), a()]), mode: s([e().min(1), a()]), status: d(["online", "stale", "offline"]), interval_seconds: s([r().gt(0).lte(3600), a()]), started_at: s([e().datetime({ offset: !0 }), a()]), heartbeat_at: s([e().datetime({ offset: !0 }), a()]), last_error_code: s([e(), a()]) }).strict()), runtime: u({ runtime_id: e().min(1), runtime_version: e().min(1), mode: d(["fake", "replay", "provider"]), provider_configured: p(), live_canary_status: d(["not_run", "passed", "failed", "unknown"]), model: s([e(), a()]), api_mode: s([e(), a()]) }).strict(), sources: o(u({ source_id: e().min(1), status: e().min(1), enabled: p(), cursor: s([e(), a()]), last_success_at: s([e().datetime({ offset: !0 }), a()]), next_poll_at: s([e().datetime({ offset: !0 }), a()]), consecutive_failures: r().int().gte(0), error_code: s([e(), a()]) }).strict()), capabilities: o(u({ capability_id: e().min(1), capability_type: e().min(1), status: e().min(1), permissions: o(e()), network_domains: o(e()), timeout_seconds: r().gt(0), max_cost_usd: s([r().gte(0), a()]) }).strict()), jobs: u({ queued: r().int().gte(0), running: r().int().gte(0), retry_wait: r().int().gte(0), pending_owner_review: r().int().gte(0), completed: r().int().gte(0), failed: r().int().gte(0), cancelled: r().int().gte(0) }).strict(), recent_jobs: o(u({ job_id: e().min(1), schema_version: h("evolution-job.v1"), trigger_key: e().min(1).max(512), trigger_type: d(["scheduled", "feedback", "failure_pattern", "evaluation_batch"]), domain_pack_ref: e().min(1), status: d(["queued", "running", "retry_wait", "pending_owner_review", "completed", "failed", "cancelled"]), stage: d(["discover", "plan", "candidate", "replay", "holdout", "shadow", "review"]), input_refs: o(e().min(1)).min(1), candidate_id: s([e(), a()]), experiment_refs: o(e().min(1)), result_refs: o(e().min(1)), attempt: r().int().gte(0), max_attempts: r().int().gte(1).lte(10), lease_owner: s([e(), a()]), lease_expires_at: s([e().datetime({ offset: !0 }), a()]), next_attempt_at: s([e().datetime({ offset: !0 }), a()]), last_error_code: s([e(), a()]), created_at: e().datetime({ offset: !0 }), updated_at: e().datetime({ offset: !0 }), finished_at: s([e().datetime({ offset: !0 }), a()]) }).strict()) }).strict();
u({ runtime_id: e().min(1), runtime_version: e().min(1), mode: d(["fake", "replay", "provider"]), provider_configured: p(), live_canary_status: d(["not_run", "passed", "failed", "unknown"]), model: s([e(), a()]), api_mode: s([e(), a()]) }).strict();
u({ evidence_id: e().min(1), title: e().min(1), snippet: e().min(1).max(2e4), source_url: e().url(), observed_at: e().datetime({ offset: !0 }), published_at: s([e().datetime({ offset: !0 }), a()]), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")) }).strict();
u({ request_id: e().min(1), capability_id: e().min(1), query: e().min(1).max(2e3), allowed_domains: o(e().min(1)), max_results: r().int().gte(1).lte(20), max_cost_usd: s([r().gte(0), a()]), observed_at: e().datetime({ offset: !0 }) }).strict();
u({ request_id: e().min(1), capability_id: e().min(1), provider: e().min(1), evidence: o(u({ evidence_id: e().min(1), title: e().min(1), snippet: e().min(1).max(2e4), source_url: e().url(), observed_at: e().datetime({ offset: !0 }), published_at: s([e().datetime({ offset: !0 }), a()]), received_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")) }).strict()), cost_usd: s([r().gte(0), a()]), completed_at: e().datetime({ offset: !0 }) }).strict();
u({ service_id: e().min(1), role: d(["api", "realtime_worker", "research_worker", "evolution_worker"]), instance_id: s([e().min(1), a()]), version: s([e().min(1), a()]), mode: s([e().min(1), a()]), status: d(["online", "stale", "offline"]), interval_seconds: s([r().gt(0).lte(3600), a()]), started_at: s([e().datetime({ offset: !0 }), a()]), heartbeat_at: s([e().datetime({ offset: !0 }), a()]), last_error_code: s([e(), a()]) }).strict();
u({ source_id: e().min(1), status: e().min(1), enabled: p(), cursor: s([e(), a()]), last_success_at: s([e().datetime({ offset: !0 }), a()]), next_poll_at: s([e().datetime({ offset: !0 }), a()]), consecutive_failures: r().int().gte(0), error_code: s([e(), a()]) }).strict();
u({ pointer_id: e().min(1), domain_pack_ref: e().min(1), candidate_id: e().min(1), generation: r().int().gte(0), updated_at: e().datetime({ offset: !0 }) }).strict();
u({ candidate_id: e().min(1), candidate_type: d(["strategy", "runtime", "profile", "doctrine", "provider_policy"]), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), version: e().min(1), parent_version: s([e(), a()]), status: d(["candidate", "experimental", "eligible", "active", "rejected", "retired"]), created_at: e().datetime({ offset: !0 }), content_ref: s([e(), a()]), source: e().min(1) }).strict();
u({ capability_id: e().min(1), version: e().min(1), capability_type: d(["source", "tool", "provider", "runtime", "strategy", "workbench"]), provider: e().min(1), license: e().min(1), input_schema_ref: e().min(1), output_schema_ref: e().min(1), permissions: o(e()), network_domains: o(e()), timeout_seconds: r().gt(0).lte(300), max_cost_usd: s([r().gte(0), a()]), status: d(["discovered", "audited", "enabled", "shadow", "rejected", "retired"]) }).strict();
u({ dataset_id: e().min(1), split: d(["replay", "holdout", "shadow", "live_observation"]), manifest_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), fixture_refs: o(e().min(1)).min(1), fixture_hashes: S(e().regex(new RegExp("^[a-f0-9]{64}$"))), cutoff_rule: h("published_at <= observed_at <= received_at"), label_rule: h("outcomes.available_at > received_at"), leakage_audit: d(["passed", "failed"]), authorization: e().min(1), visibility: h("owner_only"), source_mode: d(["fixture", "prospective"]), window_start_at: e().datetime({ offset: !0 }), window_end_at: e().datetime({ offset: !0 }), event_family_counts: S(r().int().gte(1)), created_at: e().datetime({ offset: !0 }) }).strict();
u({ datasets: o(u({ dataset_id: e().min(1), split: d(["replay", "holdout", "shadow", "live_observation"]), manifest_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), fixture_refs: o(e().min(1)).min(1), fixture_hashes: S(e().regex(new RegExp("^[a-f0-9]{64}$"))), cutoff_rule: h("published_at <= observed_at <= received_at"), label_rule: h("outcomes.available_at > received_at"), leakage_audit: d(["passed", "failed"]), authorization: e().min(1), visibility: h("owner_only"), source_mode: d(["fixture", "prospective"]), window_start_at: e().datetime({ offset: !0 }), window_end_at: e().datetime({ offset: !0 }), event_family_counts: S(r().int().gte(1)), created_at: e().datetime({ offset: !0 }) }).strict()), candidates: o(u({ candidate_id: e().min(1), candidate_type: d(["strategy", "runtime", "profile", "doctrine", "provider_policy"]), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), version: e().min(1), parent_version: s([e(), a()]), status: d(["candidate", "experimental", "eligible", "active", "rejected", "retired"]), created_at: e().datetime({ offset: !0 }), content_ref: s([e(), a()]), source: e().min(1) }).strict()), experiments: o(u({ experiment_id: e().min(1), dataset_id: e().min(1), baseline_ref: e().min(1), candidate_refs: o(e().min(1)).min(1), status: d(["registered", "running", "completed", "failed"]), created_at: e().datetime({ offset: !0 }), strategy_version: e().min(1), runtime_id: e().min(1), runtime_version: e().min(1), provider_id: s([e(), a()]), model: s([e(), a()]), schema_version: e().min(1), random_seed: s([r().int().gte(0), a()]), randomness_policy: d(["deterministic", "seeded", "provider_default"]), deadline_seconds: r().int().gte(1).lte(3600), max_cost_usd: s([r().gte(0), a()]) }).strict()), results: o(u({ result_id: e().min(1), experiment_id: e().min(1), candidate_id: e().min(1), sample_count: r().int().gte(0), brier_score: s([r().gte(0).lte(1), a()]), cost_usd: s([r().gte(0), a()]), p95_latency_ms: s([r().int().gte(0), a()]), safety_violations: r().int().gte(0), event_family_counts: S(r().int().gte(0)), created_at: e().datetime({ offset: !0 }), stage: d(["replay", "holdout", "shadow"]), failure_counts: S(r().int().gte(0)), evidence_coverage: s([r().gte(0).lte(1), a()]), directional_accuracy: s([r().gte(0).lte(1), a()]), raw_artifact_refs: o(e().min(1)).min(1), scorer_version: e().min(1) }).strict()), pointers: o(u({ pointer_id: e().min(1), domain_pack_ref: e().min(1), candidate_id: e().min(1), generation: r().int().gte(0), updated_at: e().datetime({ offset: !0 }) }).strict()), failures: o(u({ pattern_id: e().min(1), failure_code: e().min(1), occurrence_count: r().int().gte(1), impact: e().min(1), source_refs: o(e().min(1)).min(1), root_cause_hypothesis: s([e(), a()]), remediation_refs: o(e().min(1)), status: d(["open", "mitigated", "regressed", "closed"]), updated_at: e().datetime({ offset: !0 }) }).strict()), experiences: o(u({ request_id: e().min(1), domain_pack_ref: e().min(1), event_family: e().min(1), lesson: e().min(1).max(1e4), applicable_conditions: o(e()), evidence_refs: o(e().min(1)).min(1), outcome_refs: o(e().min(1)).min(1), evaluation_refs: o(e().min(1)).min(1), source_type: d(["owner", "evaluation", "failure_pattern", "agent"]), created_by: e().min(1), experience_id: e().min(1), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), status: d(["candidate", "verified", "retired"]), available_at: e().datetime({ offset: !0 }), created_at: e().datetime({ offset: !0 }) }).strict()), decisions: o(u({ decision_id: e().min(1), request_id: e().min(1), domain_pack_ref: e().min(1), candidate_id: e().min(1), owner: e().min(1), decision: d(["promote", "reject", "rollback"]), reason: e().min(1), evaluation_refs: o(e().min(1)), previous_candidate_id: s([e(), a()]), resulting_candidate_id: s([e(), a()]), resulting_generation: r().int().gte(0), created_at: e().datetime({ offset: !0 }) }).strict()) }).strict();
u({ request_id: e().min(1), domain_pack_ref: e().min(1), event_family: e().min(1), lesson: e().min(1).max(1e4), applicable_conditions: o(e()), evidence_refs: o(e().min(1)).min(1), outcome_refs: o(e().min(1)).min(1), evaluation_refs: o(e().min(1)).min(1), source_type: d(["owner", "evaluation", "failure_pattern", "agent"]), created_by: e().min(1), experience_id: e().min(1), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), status: d(["candidate", "verified", "retired"]), available_at: e().datetime({ offset: !0 }), created_at: e().datetime({ offset: !0 }) }).strict();
u({ request_id: e().min(1), domain_pack_ref: e().min(1), event_family: e().min(1), lesson: e().min(1).max(1e4), applicable_conditions: o(e()), evidence_refs: o(e().min(1)).min(1), outcome_refs: o(e().min(1)).min(1), evaluation_refs: o(e().min(1)).min(1), source_type: d(["owner", "evaluation", "failure_pattern", "agent"]), created_by: e().min(1) }).strict();
u({ experiment_id: e().min(1), dataset_id: e().min(1), baseline_ref: e().min(1), candidate_refs: o(e().min(1)).min(1), status: d(["registered", "running", "completed", "failed"]), created_at: e().datetime({ offset: !0 }), strategy_version: e().min(1), runtime_id: e().min(1), runtime_version: e().min(1), provider_id: s([e(), a()]), model: s([e(), a()]), schema_version: e().min(1), random_seed: s([r().int().gte(0), a()]), randomness_policy: d(["deterministic", "seeded", "provider_default"]), deadline_seconds: r().int().gte(1).lte(3600), max_cost_usd: s([r().gte(0), a()]) }).strict();
u({ result_id: e().min(1), experiment_id: e().min(1), candidate_id: e().min(1), sample_count: r().int().gte(0), brier_score: s([r().gte(0).lte(1), a()]), cost_usd: s([r().gte(0), a()]), p95_latency_ms: s([r().int().gte(0), a()]), safety_violations: r().int().gte(0), event_family_counts: S(r().int().gte(0)), created_at: e().datetime({ offset: !0 }), stage: d(["replay", "holdout", "shadow"]), failure_counts: S(r().int().gte(0)), evidence_coverage: s([r().gte(0).lte(1), a()]), directional_accuracy: s([r().gte(0).lte(1), a()]), raw_artifact_refs: o(e().min(1)).min(1), scorer_version: e().min(1) }).strict();
u({ pattern_id: e().min(1), failure_code: e().min(1), occurrence_count: r().int().gte(1), impact: e().min(1), source_refs: o(e().min(1)).min(1), root_cause_hypothesis: s([e(), a()]), remediation_refs: o(e().min(1)), status: d(["open", "mitigated", "regressed", "closed"]), updated_at: e().datetime({ offset: !0 }) }).strict();
u({ feedback_id: e().min(1), request_id: e().min(1), target_type: d(["run", "memo", "experiment", "candidate", "experience", "failure_pattern"]), target_id: e().min(1), created_by: e().min(1), verdict: d(["useful", "not_useful", "incorrect", "needs_review"]), notes: e().min(1).max(1e4), created_at: e().datetime({ offset: !0 }) }).strict();
u({ request_id: e().min(1), target_type: d(["run", "memo", "experiment", "candidate", "experience", "failure_pattern"]), target_id: e().min(1), created_by: e().min(1), verdict: d(["useful", "not_useful", "incorrect", "needs_review"]), notes: e().min(1).max(1e4) }).strict();
u({ decision_id: e().min(1), request_id: e().min(1), domain_pack_ref: e().min(1), candidate_id: e().min(1), owner: e().min(1), decision: d(["promote", "reject", "rollback"]), reason: e().min(1), evaluation_refs: o(e().min(1)), previous_candidate_id: s([e(), a()]), resulting_candidate_id: s([e(), a()]), resulting_generation: r().int().gte(0), created_at: e().datetime({ offset: !0 }) }).strict();
u({ request_id: e().min(1), candidate_id: e().min(1), owner: e().min(1), decision: d(["promote", "reject", "rollback"]), reason: e().min(1), evaluation_refs: o(e().min(1)), expected_generation: r().int().gte(0) }).strict();
u({ decision: u({ decision_id: e().min(1), request_id: e().min(1), domain_pack_ref: e().min(1), candidate_id: e().min(1), owner: e().min(1), decision: d(["promote", "reject", "rollback"]), reason: e().min(1), evaluation_refs: o(e().min(1)), previous_candidate_id: s([e(), a()]), resulting_candidate_id: s([e(), a()]), resulting_generation: r().int().gte(0), created_at: e().datetime({ offset: !0 }) }).strict(), active_pointer: s([u({ pointer_id: e().min(1), domain_pack_ref: e().min(1), candidate_id: e().min(1), generation: r().int().gte(0), updated_at: e().datetime({ offset: !0 }) }).strict(), a()]) }).strict();
u({ rule_id: e().min(1), status: d(["pass", "fail"]), reason_code: e().min(1), detail: e().min(1) }).strict();
u({ stage: d(["replay", "holdout", "shadow"]), result_id: e().min(1), baseline_result_id: e().min(1), sample_count: r().int().gte(0), candidate_brier_score: s([r().gte(0).lte(1), a()]), baseline_brier_score: s([r().gte(0).lte(1), a()]), brier_delta: s([r(), a()]), candidate_cost_usd: s([r().gte(0), a()]), baseline_cost_usd: s([r().gte(0), a()]), cost_ratio: s([r().gte(0), a()]), p95_latency_ms: s([r().int().gte(0), a()]), safety_violations: r().int().gte(0) }).strict();
u({ domain_pack_ref: e().min(1), candidate_id: e().min(1), evaluation_refs: o(e().min(1)), eligible: p(), checks: o(u({ rule_id: e().min(1), status: d(["pass", "fail"]), reason_code: e().min(1), detail: e().min(1) }).strict()).min(1), deltas: o(u({ stage: d(["replay", "holdout", "shadow"]), result_id: e().min(1), baseline_result_id: e().min(1), sample_count: r().int().gte(0), candidate_brier_score: s([r().gte(0).lte(1), a()]), baseline_brier_score: s([r().gte(0).lte(1), a()]), brier_delta: s([r(), a()]), candidate_cost_usd: s([r().gte(0), a()]), baseline_cost_usd: s([r().gte(0), a()]), cost_ratio: s([r().gte(0), a()]), p95_latency_ms: s([r().int().gte(0), a()]), safety_violations: r().int().gte(0) }).strict()) }).strict();
u({ candidate_id: e().min(1), evaluation_refs: o(e().min(1)) }).strict();
u({ memo_id: e().min(1), request_id: e().min(1), run_id: s([e(), a()]), snapshot_id: s([e(), a()]), domain_pack_ref: e().min(1), created_by: e().min(1), claims: o(e().min(1)).min(1), evidence_refs: o(e().min(1)).min(1), counterpoints: o(e().min(1)), uncertainties: o(e().min(1)), follow_up_questions: o(e().min(1)), status: d(["draft", "submitted", "reviewed", "accepted", "rejected", "superseded"]), created_at: e().datetime({ offset: !0 }) }).strict();
u({ request_id: e().min(1), run_id: s([e(), a()]), snapshot_id: s([e(), a()]), domain_pack_ref: e().min(1), created_by: e().min(1), claims: o(e().min(1)).min(1), evidence_refs: o(e().min(1)).min(1), counterpoints: o(e().min(1)), uncertainties: o(e().min(1)), follow_up_questions: o(e().min(1)) }).strict();
u({ memos: o(u({ memo_id: e().min(1), request_id: e().min(1), run_id: s([e(), a()]), snapshot_id: s([e(), a()]), domain_pack_ref: e().min(1), created_by: e().min(1), claims: o(e().min(1)).min(1), evidence_refs: o(e().min(1)).min(1), counterpoints: o(e().min(1)), uncertainties: o(e().min(1)), follow_up_questions: o(e().min(1)), status: d(["draft", "submitted", "reviewed", "accepted", "rejected", "superseded"]), created_at: e().datetime({ offset: !0 }) }).strict()), feedback: o(u({ feedback_id: e().min(1), request_id: e().min(1), target_type: d(["run", "memo", "experiment", "candidate", "experience", "failure_pattern"]), target_id: e().min(1), created_by: e().min(1), verdict: d(["useful", "not_useful", "incorrect", "needs_review"]), notes: e().min(1).max(1e4), created_at: e().datetime({ offset: !0 }) }).strict()), capabilities: o(u({ capability_id: e().min(1), version: e().min(1), capability_type: d(["source", "tool", "provider", "runtime", "strategy", "workbench"]), provider: e().min(1), license: e().min(1), input_schema_ref: e().min(1), output_schema_ref: e().min(1), permissions: o(e()), network_domains: o(e()), timeout_seconds: r().gt(0).lte(300), max_cost_usd: s([r().gte(0), a()]), status: d(["discovered", "audited", "enabled", "shadow", "rejected", "retired"]) }).strict()) }).strict();
u({ schema_version: h("fact-envelope.v1"), fact_id: e().min(1), evidence_id: e().min(1), requirement_id: e().min(1), metric_family: e().min(1), field: e().min(1), instrument: s([e(), a()]), venue: s([e(), a()]), value: s([r(), e(), a()]), unit: e().min(1), window_start_at: s([e().datetime({ offset: !0 }), a()]), window_end_at: s([e().datetime({ offset: !0 }), a()]), event_offset: s([e(), a()]), observed_at: e().datetime({ offset: !0 }), received_at: e().datetime({ offset: !0 }), published_at: s([e().datetime({ offset: !0 }), a()]), source_id: e().min(1), independence_group: e().min(1), quality: d(["candidate", "accepted", "rejected", "stale", "conflicted"]), delay_class: d(["realtime", "delayed", "historical", "unknown"]), payload_schema_ref: e().min(1), payload_hash: e().regex(new RegExp("^[a-f0-9]{64}$")), attributes: S(s([r(), e(), p(), a()])) }).strict();
const st = u({ evidence_id: e().min(1), source_id: e().min(1), source_type: e().min(1), observed_at: e().datetime({ offset: !0 }), published_at: s([e().datetime({ offset: !0 }), a()]), received_at: e().datetime({ offset: !0 }), cutoff_at: e().datetime({ offset: !0 }), content_hash: e().regex(new RegExp("^[a-f0-9]{64}$")) }).strict(), at = u({ mode: d(["fixed_graph", "supervisor_candidate"]), supervisor_role: s([e(), a()]), planned_capabilities: o(e()), required_capabilities: o(e()), specialist_coverage: o(e()), missing_capabilities: o(e()), replan_count: r().int().gte(0).lte(1), experiment_refs: o(e()) }).strict(), rt = u({ sequence_no: r().int().gte(0), event_type: e().min(1), occurred_at: e().datetime({ offset: !0 }), reference_type: s([e(), a()]), reference_id: s([e(), a()]) }).strict(), dt = u({ strategy_version: e().min(1), runtime_version: e().min(1), pack_version: s([e(), a()]), provider_ids: o(e()), models: o(e()), schema_versions: o(e()), pricing_versions: o(e()) }).strict();
u({ candidate_id: e().min(1), candidate_type: e().min(1), version: e().min(1), status: e().min(1), source: e().min(1), input_refs: o(e()), stages: o(d(["replay", "holdout", "shadow"])), owner_status: d(["not_ready", "pending_owner_review", "promoted", "retained", "rejected"]) }).strict();
u({ forecast_id: e().min(1), horizon: d(["30m", "24h", "72h"]), direction: d(["long", "short", "neutral", "no_trade"]), quality_status: e().min(1), return_pct: s([r(), a()]), net_return_pct: s([r(), a()]), brier_score: s([r().gte(0).lte(1), a()]), direction_correct: s([p(), a()]), mfe_pct: s([r(), a()]), mae_pct: s([r(), a()]), unavailable_reason: s([e(), a()]), observed_at: s([e().datetime({ offset: !0 }), a()]) }).strict();
u({ requirement_id: e().min(1), importance: d(["hard", "soft"]), status: d(["ready", "missing", "stale", "rejected", "conflict", "no_baseline", "window_missing", "tool_unavailable"]), reason_code: s([e(), a()]), required_fields: o(e()), missing_fields: o(e()), required_event_offsets: o(e()), attempted_capabilities: o(e()), next_capability: s([e(), a()]) }).strict();
u({ status: d(["known", "partial", "unknown"]), known_subtotal_usd: s([r().gte(0), a()]), total_usd: s([r().gte(0), a()]), currency: h("USD"), unknown_components: o(e()), components: o(u({ component: d(["model", "search", "typed_provider", "subscription"]), amount_usd: s([r().gte(0), a()]), status: d(["known", "known_free", "unknown"]), policy_ref: s([e(), a()]) }).strict()) }).strict();
u({ component: d(["model", "search", "typed_provider", "subscription"]), amount_usd: s([r().gte(0), a()]), status: d(["known", "known_free", "unknown"]), policy_ref: s([e(), a()]) }).strict();
u({ schema_version: h("research-inbox-item.v1"), event_id: e().min(1), event_title: e().min(1), event_family: s([e(), a()]), watch_id: s([e(), a()]), run_id: s([e().min(1), a()]), dsh_session_id: s([e(), a()]), artifact_id: s([e(), a()]), parent_run_id: s([e(), a()]), admission_origin: d(["manual", "automatic", "scheduled_recheck", "legacy"]), status: d(["watching", "queued", "researching", "report_ready", "research_only", "rejected", "failed", "cancelled"]), baseline_status: d(["not_applicable", "pending", "ready", "unavailable"]), gate_status: s([h("publish"), h("degraded"), h("research_only"), h("reject"), h(null)]), report_available: p(), headline: s([e(), a()]), summary: s([e(), a()]), scheduled_at: s([e().datetime({ offset: !0 }), a()]), next_recheck_at: s([e().datetime({ offset: !0 }), a()]), notification_status: d(["not_applicable", "pending", "retry_wait", "delivered", "failed"]), domain_pack_ref: e().min(1), role_profile_ref: e().min(1), created_at: e().datetime({ offset: !0 }), updated_at: e().datetime({ offset: !0 }) }).strict();
const Zt = u({ schema_version: h("research-inbox-view.v1"), items: o(u({ schema_version: h("research-inbox-item.v1"), event_id: e().min(1), event_title: e().min(1), event_family: s([e(), a()]), watch_id: s([e(), a()]), run_id: s([e().min(1), a()]), dsh_session_id: s([e(), a()]), artifact_id: s([e(), a()]), parent_run_id: s([e(), a()]), admission_origin: d(["manual", "automatic", "scheduled_recheck", "legacy"]), status: d(["watching", "queued", "researching", "report_ready", "research_only", "rejected", "failed", "cancelled"]), baseline_status: d(["not_applicable", "pending", "ready", "unavailable"]), gate_status: s([h("publish"), h("degraded"), h("research_only"), h("reject"), h(null)]), report_available: p(), headline: s([e(), a()]), summary: s([e(), a()]), scheduled_at: s([e().datetime({ offset: !0 }), a()]), next_recheck_at: s([e().datetime({ offset: !0 }), a()]), notification_status: d(["not_applicable", "pending", "retry_wait", "delivered", "failed"]), domain_pack_ref: e().min(1), role_profile_ref: e().min(1), created_at: e().datetime({ offset: !0 }), updated_at: e().datetime({ offset: !0 }) }).strict()), generated_at: e().datetime({ offset: !0 }) }).strict(), It = u({ schema_version: h("research-observability-view.v1"), run_id: e().min(1), versions: u({ domain_pack_ref: e().min(1), domain_pack_version: e().min(1), role_profile_ref: e().min(1), runtime_id: e().min(1), runtime_version: e().min(1), capability_versions: o(e().min(1)), gate_policy_ref: e().min(1), source_registry_ref: e().min(1) }).strict(), readiness: o(u({ requirement_id: e().min(1), importance: d(["hard", "soft"]), status: d(["ready", "missing", "stale", "rejected", "conflict", "no_baseline", "window_missing", "tool_unavailable"]), reason_code: s([e(), a()]), required_fields: o(e()), missing_fields: o(e()), required_event_offsets: o(e()), attempted_capabilities: o(e()), next_capability: s([e(), a()]) }).strict()), source_attempts: o(u({ capability_id: e().min(1), provider_id: e().min(1), route_role: d(["primary", "fallback", "direct"]), service_tier: d(["free_proxy", "licensed_live", "replay", "unknown"]), status: d(["succeeded", "failed", "skipped", "denied", "timed_out"]), latency_ms: s([r().int().gte(0), a()]), cost_usd: s([r().gte(0), a()]), cost_status: d(["known", "unknown"]), error_code: s([e(), a()]), retryable: p() }).strict()), cost: u({ status: d(["known", "partial", "unknown"]), known_subtotal_usd: s([r().gte(0), a()]), total_usd: s([r().gte(0), a()]), currency: h("USD"), unknown_components: o(e()), components: o(u({ component: d(["model", "search", "typed_provider", "subscription"]), amount_usd: s([r().gte(0), a()]), status: d(["known", "known_free", "unknown"]), policy_ref: s([e(), a()]) }).strict()) }).strict(), trajectory_ref: s([e(), a()]), telemetry_ref: s([e(), a()]), ledger_ref: e().min(1), generated_at: e().datetime({ offset: !0 }) }).strict(), Vt = u({ schema_version: h("research-value-evaluation.v1"), evaluation_id: e().min(1), run_id: e().min(1), artifact_id: s([e(), a()]), evaluation_version: e().min(1), mode: d(["directional", "research_only", "failed"]), hard_coverage_ratio: r().gte(0).lte(1), soft_coverage_ratio: r().gte(0).lte(1), accepted_evidence_count: r().int().gte(0), rejected_evidence_count: r().int().gte(0), typed_fact_count: r().int().gte(0), baseline_status: d(["not_applicable", "pending", "ready", "unavailable"]), latency_ms: s([r().int().gte(0), a()]), cost_status: d(["known", "partial", "unknown"]), known_cost_usd: s([r().gte(0), a()]), citation_traceability: r().gte(0).lte(1), usefulness: d(["useful", "partial", "not_useful", "unlabeled"]), terminal_reason: s([e(), a()]), outcomes: o(u({ forecast_id: e().min(1), horizon: d(["30m", "24h", "72h"]), direction: d(["long", "short", "neutral", "no_trade"]), quality_status: e().min(1), return_pct: s([r(), a()]), net_return_pct: s([r(), a()]), brier_score: s([r().gte(0).lte(1), a()]), direction_correct: s([p(), a()]), mfe_pct: s([r(), a()]), mae_pct: s([r(), a()]), unavailable_reason: s([e(), a()]), observed_at: s([e().datetime({ offset: !0 }), a()]) }).strict()), evaluated_at: e().datetime({ offset: !0 }) }).strict();
u({ domain_pack_ref: e().min(1), domain_pack_version: e().min(1), role_profile_ref: e().min(1), runtime_id: e().min(1), runtime_version: e().min(1), capability_versions: o(e().min(1)), gate_policy_ref: e().min(1), source_registry_ref: e().min(1) }).strict();
u({ capability_id: e().min(1), provider_id: e().min(1), route_role: d(["primary", "fallback", "direct"]), service_tier: d(["free_proxy", "licensed_live", "replay", "unknown"]), status: d(["succeeded", "failed", "skipped", "denied", "timed_out"]), latency_ms: s([r().int().gte(0), a()]), cost_usd: s([r().gte(0), a()]), cost_status: d(["known", "unknown"]), error_code: s([e(), a()]), retryable: p() }).strict();
const ot = d(["admitted", "running", "completed", "degraded", "failed", "cancelled"]), Se = d(["publish", "degraded", "research_only", "reject"]), ct = d(["long", "short", "neutral", "no_trade"]);
u({
  source_id: e().min(1),
  source_type: e().min(1),
  version: e().min(1),
  capabilities: o(e()),
  authority_level: e(),
  poll_interval_seconds: r().positive(),
  max_batch: r().int().positive(),
  allowed_domains: o(e()).default([]),
  enabled: p()
});
const ut = u({
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
u({
  instrument: e(),
  observed_at: e(),
  received_at: e(),
  bid: r().nullable(),
  ask: r().nullable(),
  last: r().nullable(),
  volume: r().nullable(),
  source_id: e(),
  quality_status: d(["observed", "estimated", "unavailable"]),
  benchmark: d(["first_executable", "vwap_1m", "last", "none"])
});
u({
  artifact_id: e(),
  channel: e(),
  dedupe_key: e(),
  subject: e(),
  body: e(),
  created_at: e()
});
u({
  status: d(["ok", "degraded"]),
  running_runs: r().int().nonnegative(),
  failed_runs: r().int().nonnegative(),
  sources: o(ut)
});
const lt = u({
  check_id: e().min(1),
  status: d(["pass", "fail", "warning"]),
  detail: e().min(1),
  error_code: e().nullable()
});
u({
  schema_version: h("pilot-readiness.v1"),
  status: d(["ready", "not_ready"]),
  checked_at: e(),
  pilot_mode: p(),
  notification_channel: d(["local", "email"]),
  source_ids: o(e()),
  checks: o(lt),
  live_canaries_required: o(e())
});
const mt = u({
  forecast_id: e(),
  artifact_id: e(),
  instrument: e(),
  horizon: e(),
  direction: ct,
  probability: r().min(0).max(1),
  trigger: e(),
  invalidation: e(),
  expires_at: e()
}), je = u({
  run_id: e(),
  event_id: e(),
  status: ot,
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
  gate_status: Se.nullable()
}), _t = u({
  artifact_id: e(),
  run_id: e(),
  event_id: e(),
  gate_status: Se,
  headline: e(),
  summary: e(),
  facts: o(e()),
  inferences: o(e()),
  counter_thesis: e(),
  uncertainty: o(e()),
  transmission_chain: o(e()),
  citations: o(e()),
  forecasts: o(mt),
  gate_decisions: o(u({ rule_id: e(), status: e(), reason_code: e(), input_hash: e() })),
  created_at: e()
});
u({
  inbox: u({ pending_count: r(), running_count: r(), latest: o(je) }),
  published_count_30d: r(),
  forecast_count: r(),
  evaluated_count: r(),
  health_status: e(),
  active_strategy: e(),
  active_pack: e()
});
const ft = u({
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
  retryable: p()
}), pt = u({
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
u({
  run: je,
  timeline: o(rt),
  steps: o(pt),
  calls: o(ft),
  artifact: _t.nullable(),
  evaluation_count: r(),
  evaluations: o(u({
    evaluation_id: e(),
    forecast_id: e(),
    brier_score: r(),
    net_return_pct: r(),
    direction_correct: p(),
    label_status: e(),
    evaluated_at: e()
  })),
  snapshot_cutoff_at: e().nullable(),
  snapshot_hash: e().nullable(),
  evidence_lineage: o(st),
  versions: dt,
  orchestration: at
}).strict();
export {
  Z,
  bt as a,
  vt as b,
  Zt as c,
  qt as d,
  It as e,
  Vt as f,
  Ct as g,
  Tt as h,
  kt as i,
  Ot as j,
  At as k,
  gt as l,
  ht as m,
  yt as n,
  xt as o,
  $t as p,
  Rt as q,
  zt as r,
  wt as s,
  St as t,
  Et as u,
  jt as v,
  Nt as w
};
//# sourceMappingURL=index-CWciLEwK.js.map
