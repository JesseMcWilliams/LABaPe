{
	"subject": {{ toJson .Subject }},
	"sans": {{ toJson .SANs }},
{{- if typeIs "*rsa.PublicKey" .Insecure.CR.PublicKey }}
	"keyUsage": {{ if .Insecure.User.ku }}{{ toJson .Insecure.User.ku }}{{ else }}["keyEncipherment", "digitalSignature"]{{ end }},
{{- else }}
	"keyUsage": {{ if .Insecure.User.ku }}{{ toJson .Insecure.User.ku }}{{ else }}["digitalSignature"]{{ end }},
{{- end }}
	"extKeyUsage": {{ if .Insecure.User.eku }}{{ toJson .Insecure.User.eku }}{{ else }}["serverAuth", "clientAuth"]{{ end }}
}
