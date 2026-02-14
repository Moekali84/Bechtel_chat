import pbiPlugin from "eslint-plugin-powerbi-visuals";

export default [
    pbiPlugin.configs.recommended,
    {
        rules: {
            // innerHTML is used extensively for chat rendering — sanitized via escapeHtml()
            "powerbi-visuals/no-inner-outer-html": "warn",
            // Backend URL is user-configurable and may be http://localhost during dev
            "powerbi-visuals/no-http-string": ["error", [
                "http://www.example.com/?.*",
                "http://localhost:?.*",
                "^http:\\/\\/www.w3.org\\/2000\\/svg"
            ]]
        }
    }
];
