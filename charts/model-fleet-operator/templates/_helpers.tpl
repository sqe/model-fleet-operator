{{- define "model-fleet.name" -}}{{ default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}{{- end }}
{{- define "model-fleet.fullname" -}}{{ default (printf "%s-%s" .Release.Name (include "model-fleet.name" .)) .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}{{- end }}
{{- define "model-fleet.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | quote }}
app.kubernetes.io/name: {{ include "model-fleet.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}
{{- define "model-fleet.operatorSA" -}}{{ default (printf "%s-operator" (include "model-fleet.fullname" .)) .Values.serviceAccount.operator.name }}{{- end }}
{{- define "model-fleet.slackSA" -}}{{ default (printf "%s-slack" (include "model-fleet.fullname" .)) .Values.serviceAccount.slack.name }}{{- end }}
{{- define "model-fleet.controlPlaneSA" -}}{{ printf "%s-control-plane" (include "model-fleet.fullname" .) }}{{- end }}
