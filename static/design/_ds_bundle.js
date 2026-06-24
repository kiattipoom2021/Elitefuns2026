/* @ds-bundle: {"format":3,"namespace":"DesignMDDesignSystem_9cde88","components":[{"name":"Input","sourcePath":"components/content/Input.jsx"},{"name":"PortraitCard","sourcePath":"components/content/PortraitCard.jsx"},{"name":"Badge","sourcePath":"components/core/Badge.jsx"},{"name":"Button","sourcePath":"components/core/Button.jsx"},{"name":"Eyebrow","sourcePath":"components/core/Eyebrow.jsx"},{"name":"IconButton","sourcePath":"components/core/IconButton.jsx"}],"sourceHashes":{"components/content/Input.jsx":"8fb6954f17bb","components/content/PortraitCard.jsx":"d49d140cc9dd","components/core/Badge.jsx":"40323ac46b67","components/core/Button.jsx":"a85823376740","components/core/Eyebrow.jsx":"a0245252d8ba","components/core/IconButton.jsx":"7a57bce916b8"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.DesignMDDesignSystem_9cde88 = window.DesignMDDesignSystem_9cde88 || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// components/content/Input.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * designMD — Input
 * Pill text field. Hairline ink border, generous padding, optional leading
 * label that sits inline as a quiet eyebrow. Newsletter / country-select /
 * search use this shape.
 */
function Input({
  type = 'text',
  placeholder,
  value,
  onChange,
  trailing = null,
  // e.g. an inline submit IconButton
  ariaLabel,
  style = {},
  ...rest
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      padding: '6px 8px 6px 24px',
      borderRadius: 'var(--radius-pill)',
      background: 'var(--white)',
      border: '1.5px solid var(--border-ink)',
      ...style
    }
  }, /*#__PURE__*/React.createElement("input", _extends({
    type: type,
    placeholder: placeholder,
    value: value,
    onChange: onChange,
    "aria-label": ariaLabel || placeholder,
    style: {
      flex: 1,
      minWidth: 0,
      border: 'none',
      outline: 'none',
      background: 'transparent',
      fontFamily: 'var(--font-sans)',
      fontSize: 16,
      fontWeight: 450,
      letterSpacing: '-0.2px',
      color: 'var(--text-primary)',
      padding: '10px 0'
    }
  }, rest)), trailing);
}
Object.assign(__ds_scope, { Input });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/content/Input.jsx", error: String((e && e.message) || e) }); }

// components/content/PortraitCard.jsx
try { (() => {
/**
 * designMD — PortraitCard
 * The signature unit: a circular portrait with a docked white "satellite"
 * arrow CTA, an optional category chip, and a title beneath. This is the
 * orbit motif made into a component.
 */
function PortraitCard({
  image,
  // url for the circular portrait
  imageStyle = {},
  // e.g. a gradient fallback
  category,
  // chip label, optional
  title,
  meta,
  // small line under title, optional
  size = 240,
  onActivate,
  style = {}
}) {
  return /*#__PURE__*/React.createElement("div", {
    style: {
      width: size,
      ...style
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      position: 'relative',
      width: size,
      height: size
    }
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      width: size,
      height: size,
      borderRadius: '50%',
      overflow: 'hidden',
      background: image ? `center/cover no-repeat url("${image}")` : 'var(--yellow-200)',
      boxShadow: 'var(--elev-2)',
      ...imageStyle
    }
  }), category && /*#__PURE__*/React.createElement("span", {
    style: {
      position: 'absolute',
      top: 16,
      left: 16,
      display: 'inline-flex',
      alignItems: 'center',
      padding: '7px 16px',
      borderRadius: 'var(--radius-pill)',
      background: 'var(--white)',
      color: 'var(--ink)',
      fontFamily: 'var(--font-sans)',
      fontSize: 13,
      fontWeight: 500,
      letterSpacing: '-0.2px',
      boxShadow: 'var(--elev-1)'
    }
  }, category), /*#__PURE__*/React.createElement("button", {
    "aria-label": typeof title === 'string' ? title : 'Open',
    onClick: onActivate,
    style: {
      position: 'absolute',
      right: Math.round(size * 0.03),
      bottom: Math.round(size * 0.03),
      width: Math.round(size * 0.22),
      height: Math.round(size * 0.22),
      minWidth: 48,
      minHeight: 48,
      borderRadius: '50%',
      border: 'none',
      background: 'var(--white)',
      color: 'var(--ink)',
      boxShadow: 'var(--elev-1)',
      cursor: 'pointer',
      fontSize: Math.round(size * 0.09),
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      transition: 'transform var(--dur-quick) var(--ease-soft)'
    },
    onMouseDown: e => {
      e.currentTarget.style.transform = 'scale(0.92)';
    },
    onMouseUp: e => {
      e.currentTarget.style.transform = 'scale(1)';
    },
    onMouseLeave: e => {
      e.currentTarget.style.transform = 'scale(1)';
    }
  }, "\u2192")), title && /*#__PURE__*/React.createElement("h3", {
    style: {
      margin: '22px 0 0',
      fontFamily: 'var(--font-display)',
      fontWeight: 500,
      fontSize: 22,
      lineHeight: 1.18,
      letterSpacing: '-0.44px',
      color: 'var(--text-primary)'
    }
  }, title), meta && /*#__PURE__*/React.createElement("p", {
    style: {
      margin: '8px 0 0',
      fontSize: 15,
      fontWeight: 450,
      color: 'var(--text-muted)'
    }
  }, meta));
}
Object.assign(__ds_scope, { PortraitCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/content/PortraitCard.jsx", error: String((e && e.message) || e) }); }

// components/core/Badge.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * designMD — Badge
 * A full-pill chip. 'chip' is the white category tag overlaid on media
 * (e.g. "Story"); 'tint' is a soft yellow label; 'ink' is a solid marker.
 */
function Badge({
  children,
  variant = 'chip',
  // 'chip' | 'tint' | 'ink' | 'outline'
  style = {},
  ...rest
}) {
  const variants = {
    chip: {
      background: 'var(--white)',
      color: 'var(--ink)',
      border: '1px solid var(--border-soft)'
    },
    tint: {
      background: 'var(--yellow-100)',
      color: 'var(--yellow-700)',
      border: 'none'
    },
    ink: {
      background: 'var(--ink)',
      color: 'var(--cream-canvas)',
      border: 'none'
    },
    outline: {
      background: 'transparent',
      color: 'var(--ink)',
      border: '1.5px solid var(--border-ink)'
    }
  }[variant];
  return /*#__PURE__*/React.createElement("span", _extends({
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      gap: 6,
      padding: '8px 18px',
      borderRadius: 'var(--radius-pill)',
      fontFamily: 'var(--font-sans)',
      fontSize: 14,
      fontWeight: 500,
      letterSpacing: '-0.2px',
      lineHeight: 1,
      whiteSpace: 'nowrap',
      ...variants,
      ...style
    }
  }, rest), children);
}
Object.assign(__ds_scope, { Badge });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Badge.jsx", error: String((e && e.message) || e) }); }

// components/core/Button.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * designMD — Button
 * The pill CTA. Ink is the primary marketing action; yellow "signal"
 * is reserved for consent / legal / utility. Radius is always 20px on
 * body buttons (never the generic 8–12 middle ground).
 */
function Button({
  children,
  variant = 'primary',
  // 'primary' | 'secondary' | 'signal'
  size = 'md',
  // 'sm' | 'md' | 'lg'
  iconRight = null,
  iconLeft = null,
  disabled = false,
  as = 'button',
  style = {},
  ...rest
}) {
  const Tag = as;
  const sizes = {
    sm: {
      padding: '4px 18px',
      fontSize: 14,
      minHeight: 34,
      radius: 20
    },
    md: {
      padding: '6px 24px',
      fontSize: 16,
      minHeight: 40,
      radius: 20
    },
    lg: {
      padding: '16px 40px',
      fontSize: 18,
      minHeight: 56,
      radius: 40
    }
  }[size];
  const variants = {
    primary: {
      background: 'var(--cta-bg)',
      color: 'var(--cta-text)',
      border: '1.5px solid var(--cta-border)',
      fontWeight: 500
    },
    secondary: {
      background: 'var(--surface-raised)',
      color: 'var(--text-primary)',
      border: '1.5px solid var(--border-ink)',
      fontWeight: 450
    },
    signal: {
      background: 'var(--signal-bg)',
      color: 'var(--signal-text)',
      border: '1.5px solid var(--signal-bg)',
      fontWeight: 500
    }
  }[variant];
  return /*#__PURE__*/React.createElement(Tag, _extends({
    disabled: Tag === 'button' ? disabled : undefined,
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      gap: 8,
      padding: sizes.padding,
      minHeight: sizes.minHeight,
      borderRadius: sizes.radius,
      fontFamily: 'var(--font-sans)',
      fontSize: sizes.fontSize,
      lineHeight: 1.2,
      letterSpacing: '-0.32px',
      cursor: disabled ? 'not-allowed' : 'pointer',
      opacity: disabled ? 0.45 : 1,
      textDecoration: 'none',
      whiteSpace: 'nowrap',
      transition: 'transform var(--dur-quick) var(--ease-soft), background var(--dur-quick) var(--ease-soft)',
      ...variants,
      ...style
    },
    onMouseDown: e => {
      if (!disabled) e.currentTarget.style.transform = 'scale(0.97)';
    },
    onMouseUp: e => {
      e.currentTarget.style.transform = 'scale(1)';
    },
    onMouseLeave: e => {
      e.currentTarget.style.transform = 'scale(1)';
    }
  }, rest), iconLeft, children, iconRight);
}
Object.assign(__ds_scope, { Button });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Button.jsx", error: String((e && e.message) || e) }); }

// components/core/Eyebrow.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * designMD — Eyebrow
 * The identity signal: a tiny yellow accent dot + uppercase, +4%-tracked
 * label. Used as the section-category marker above every title. Never omit
 * the dot.
 */
function Eyebrow({
  children,
  color = 'var(--text-primary)',
  style = {},
  ...rest
}) {
  return /*#__PURE__*/React.createElement("span", _extends({
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      gap: 8,
      fontFamily: 'var(--font-sans)',
      fontSize: 14,
      fontWeight: 700,
      letterSpacing: '0.56px',
      lineHeight: 1,
      textTransform: 'uppercase',
      color,
      ...style
    }
  }, rest), /*#__PURE__*/React.createElement("span", {
    "aria-hidden": "true",
    style: {
      width: 8,
      height: 8,
      borderRadius: '50%',
      background: 'var(--accent-dot)',
      flex: 'none'
    }
  }), children);
}
Object.assign(__ds_scope, { Eyebrow });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/Eyebrow.jsx", error: String((e && e.message) || e) }); }

// components/core/IconButton.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
/**
 * designMD — IconButton
 * Circular icon control. The 'satellite' variant is the signature white
 * micro-CTA that docks onto a circular portrait. 'outline' is for carousel
 * / utility controls; 'ghost' sits over media.
 */
function IconButton({
  children,
  // an icon node (svg / glyph)
  variant = 'satellite',
  // 'satellite' | 'outline' | 'ghost'
  size = 54,
  ariaLabel,
  style = {},
  ...rest
}) {
  const variants = {
    satellite: {
      background: 'var(--white)',
      color: 'var(--ink)',
      border: 'none',
      boxShadow: 'var(--elev-1)'
    },
    outline: {
      background: 'transparent',
      color: 'var(--ink)',
      border: '1.5px solid var(--border-ink)',
      boxShadow: 'none'
    },
    ghost: {
      background: 'rgba(255,255,255,0.92)',
      color: 'var(--ink)',
      border: 'none',
      boxShadow: 'var(--elev-1)'
    }
  }[variant];
  return /*#__PURE__*/React.createElement("button", _extends({
    "aria-label": ariaLabel,
    style: {
      width: size,
      height: size,
      borderRadius: '50%',
      display: 'inline-flex',
      alignItems: 'center',
      justifyContent: 'center',
      fontSize: Math.round(size * 0.38),
      cursor: 'pointer',
      padding: 0,
      flex: 'none',
      transition: 'transform var(--dur-quick) var(--ease-soft)',
      ...variants,
      ...style
    },
    onMouseDown: e => {
      e.currentTarget.style.transform = 'scale(0.94)';
    },
    onMouseUp: e => {
      e.currentTarget.style.transform = 'scale(1)';
    },
    onMouseLeave: e => {
      e.currentTarget.style.transform = 'scale(1)';
    }
  }, rest), children);
}
Object.assign(__ds_scope, { IconButton });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/core/IconButton.jsx", error: String((e && e.message) || e) }); }

__ds_ns.Input = __ds_scope.Input;

__ds_ns.PortraitCard = __ds_scope.PortraitCard;

__ds_ns.Badge = __ds_scope.Badge;

__ds_ns.Button = __ds_scope.Button;

__ds_ns.Eyebrow = __ds_scope.Eyebrow;

__ds_ns.IconButton = __ds_scope.IconButton;

})();
