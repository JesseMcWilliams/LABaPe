{
	"subject": {{ toJson .Subject }},
	"sans": {{ toJson .SANs }},
{{- if typeIs "*rsa.PublicKey" .Insecure.CR.PublicKey }}
	"keyUsage": {{ if .Token.ku }}{{ toJson .Token.ku }}{{ else }}["keyEncipherment", "digitalSignature"]{{ end }},
{{- else }}
	"keyUsage": {{ if .Token.ku }}{{ toJson .Token.ku }}{{ else }}["digitalSignature"]{{ end }},
{{- end }}
	"extKeyUsage": {{ if .Token.eku }}{{ toJson .Token.eku }}{{ else }}["serverAuth", "clientAuth"]{{ end }}
}
