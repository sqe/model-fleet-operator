{{- define "model-fleet.require" -}}
{{- if not .Values.clusterName }}{{ fail "clusterName is required" }}{{ end -}}
{{- if not .Values.discoveryTag }}{{ fail "discoveryTag is required" }}{{ end -}}
{{- if not .Values.nodeRoleName }}{{ fail "nodeRoleName is required" }}{{ end -}}
{{- end -}}
