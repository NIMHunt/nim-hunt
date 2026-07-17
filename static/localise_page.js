import { applyStaticInterfaceText } from './interface_text.js?v=qol-v1-20260717';

// Module scripts run after the document has been parsed. This pass translates
// only elements explicitly marked with data-i18n attributes, so public Spot
// titles, descriptions, display names, and other user-generated text are never
// touched by the localisation framework.
applyStaticInterfaceText();
