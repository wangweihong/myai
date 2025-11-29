#!/usr/bin/env python3
import argparse
import os
import platform
import shlex
import shutil
import socket
import subprocess
import sys
import textwrap
import urllib.request
from pathlib import Path
from typing import Dict, Optional

# 设计思路：
# 1. 脚本提供service和agent两种stack. 
#   * service提供promemthes+grafana stack
#   * agent提供otel agent负责节点监控,日志和后续tracing代理。 所有agent上的指标如node-exporter/dcgm-exporter由otel agent负责抓取
#   * otel agent统一推送到远端的promtheus服务。
# 2. 后续考虑将fluent-bit等日志功能集成进来使用
# 3. 下一步将agent上指标容器全部设置仅本地访问。

# 获取当前脚本所在目录
script_dir = Path(__file__).resolve().parent
# 设置默认目录为当前脚本目录下的 stack 子目录
default_stack_dir = script_dir / "monitor" / "service"
default_agent_dir = script_dir / "monitor" / "agent"
default_grafana_provision_dir = default_stack_dir / "grafana/provisioning" 
# 获取环境变量或使用默认值
monitorStackDir = os.environ.get("MONITOR_STACK_DIR", str(default_stack_dir))
monitorAgentDir = os.environ.get("MONITOR_AGENT_DIR", str(default_agent_dir))
grafanaProvisionDir= os.environ.get("MONITOR_STACK_DIR", str(default_grafana_provision_dir))

# 镜像
grafanaImage = os.environ.get("GRAFANA_IMAGE", "registry.cn-hangzhou.aliyuncs.com/eazycloud/grafana:latest")
prometheusImage = os.environ.get("PROMETHEUS_IMAGE", "registry.cn-hangzhou.aliyuncs.com/eazycloud/prometheus:latest")
nvidiaDcgmExporterImage=os.environ.get("NVIDIA_DCGM_EXPORTER_IMAGE", "registry.cn-hangzhou.aliyuncs.com/eazycloud/nvidia_dcgm-exporter:latest")
nvidiaGpuExporterImage=os.environ.get("NVIDIA_GPU_EXPORTER_IMAGE", "registry.cn-hangzhou.aliyuncs.com/eazycloud/utkuozdemir_nvidia_gpu_exporter:1.3.1")
processExporterImage=os.environ.get("PROCESS_EXPORTER_IMAGE", "registry.cn-hangzhou.aliyuncs.com/eazycloud/process-exporter:latest")
nodeExporterImage=os.environ.get("NODE_EXPORTER_IMAGE", "registry.cn-hangzhou.aliyuncs.com/eazycloud/node-exporter:latest")
otelCollectorImage=os.environ.get("OTEL_COLLECTOR_IMAGE","registry.cn-hangzhou.aliyuncs.com/eazycloud/opentelemetry-collector-contrib:0.128.0")
fluentbitImage=os.environ.get("FLUENT_BIT_IMAGE","cr.fluentbit.io/fluent/fluent-bit")
# 配置
grafanaPassword = os.environ.get("GRAFANA_PASSWORD","admin123")

class SystemdService:
    # 默认服务模板（包含可格式化的占位符）
    DEFAULT_TEMPLATE = """
[Unit]
Description={description}
After={after_targets}

[Service]
Type={service_type}
ExecStart={exec_start}
Restart={restart_policy}
ExecReload={reload_policy}
RestartSec={restart_sec}
WorkingDirectory={working_directory}
User={user}
Environment={environment}
[Install]
WantedBy={wanted_by}
"""
    def __init__(self, 
                 service_name: str, 
                 custom_template: Optional[str] = None):
        """
        初始化 systemd 服务管理器
        
        :param service_name: 服务名称（不含 .service 后缀）
        :param custom_template: 可选的自定义模板字符串（覆盖默认模板）
        """
        self.service_name = service_name
        self.service_file = Path(f"/etc/systemd/system/{service_name}.service")
        self.template = custom_template if custom_template else self.DEFAULT_TEMPLATE
        
    def generate_config(self, 
                        exec_start: str,
                        template_params: Optional[Dict] = None,
                        **kwargs) -> bool:
        """
        生成 systemd 服务配置文件
        
        :param exec_start: 必须的启动命令
        :param template_params: 模板格式化参数字典
        :param kwargs: 其他模板参数（会与template_params合并）
        :return: 是否成功生成
        """
        # 合并模板参数
        params = {
            "description": f"{self.service_name} Service",
            "after_targets": "network.target syslog.target",
            "service_type": "simple",
            "restart_policy": "always",
            "restart_sec": "5s",
            "working_directory": "/",
            "user": "root",
            "environment": "",
            "wanted_by": "multi-user.target",
            "exec_start": exec_start,
            "reload_policy": "/bin/kill -HUB $MAINPID",
        }
        
        # 更新默认参数
        if template_params:
            params.update(template_params)
        params.update(kwargs)
        
        # 处理环境变量（如果提供）
        if "environment" in params and isinstance(params["environment"], dict):
            env_str = " ".join([f"{k}={shlex.quote(str(v))}" for k, v in params["environment"].items()])
            params["environment"] = env_str
        
        try:
            # 格式化模板
            config_content = self.template.format(**params)
            
            # 需要root权限写入系统目录
            if os.geteuid() != 0:
                raise PermissionError("Root privileges required to write systemd files")
                
            self.service_file.write_text(config_content)
            print(f"✅ 服务配置文件已生成: {self.service_file}")
            return True
        except KeyError as e:
            print(f"❌ 模板缺少必要的占位符: {{{e.args[0]}}}")
            return False
        except Exception as e:
            print(f"❌ 配置文件生成失败: {str(e)}")
            return False
            
    def reload_daemon(self) -> bool:
        """重载 systemd 守护进程"""
        try:
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            print("✅ systemd 守护进程已重载")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ 重载守护进程失败: {str(e)}")
            return False
            
    def start(self) -> bool:
        """启动并启用开机自启"""
        try:
            subprocess.run(["systemctl", "enable", self.service_name, "--now"], check=True)
            print(f"✅ 服务 {self.service_name} 已启动并启用开机自启")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ 启动服务失败: {str(e)}")
            return False
    
    def stop_and_disable(self) -> bool:
        """停止并禁用开机自启"""
        try:
            subprocess.run(["systemctl", "stop", self.service_name], check=True)
            subprocess.run(["systemctl", "disable", self.service_name], check=True)
            print(f"✅ 服务 {self.service_name} 已停止并禁用开机自启")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ 停止服务失败: {str(e)}")
            return False    
            
    def is_active(self) -> bool:
        """检查服务是否处于活动状态"""
        try:
            result = subprocess.run(
                ["systemctl", "is-active", self.service_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False
            )
            return result.stdout.strip() == "active"
        except Exception:
            return False
            
    def status(self) -> str:
        """获取服务详细状态"""
        try:
            result = subprocess.run(
                ["systemctl", "status", self.service_name],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False
            )
            return result.stdout
        except Exception as e:
            return f"获取状态失败: {str(e)}"
            
    def restart(self) -> bool:
        """重启服务"""
        try:
            subprocess.run(["systemctl", "restart", self.service_name], check=True)
            print(f"✅ 服务 {self.service_name} 已重启")
            return True
        except subprocess.CalledProcessError as e:
            print(f"❌ 重启服务失败: {str(e)}")

class RawString(str):
    def __format__(self, format_spec):
        return self

# r"""""": 类似于golang的``,保留原始字符串，不做任何转义。避免python处理{},$这些特殊字符
dcmp_exporter_dashboard=r"""
   {
    "__requires": [
        {
        "type": "panel",
        "id": "gauge",
        "name": "Gauge",
        "version": ""
        },
        {
        "type": "grafana",
        "id": "grafana",
        "name": "Grafana",
        "version": "6.7.3"
        },
        {
        "type": "panel",
        "id": "graph",
        "name": "Graph",
        "version": ""
        },
        {
        "type": "datasource",
        "id": "prometheus",
        "name": "Prometheus",
        "version": "1.0.0"
        }
    ],
    "annotations": {
        "list": [
        {
            "$$hashKey": "object:192",
            "builtIn": 1,
            "datasource": "-- Grafana --",
            "enable": true,
            "hide": true,
            "iconColor": "rgba(0, 211, 255, 1)",
            "name": "Annotations & Alerts",
            "type": "dashboard"
        }
        ]
    },
    "description": "This dashboard is to display the metrics from DCGM Exporter on a Kubernetes (1.19+) cluster",
    "editable": true,
    "gnetId": 12239,
    "graphTooltip": 0,
    "id": null,
    "iteration": 1588401887165,
    "links": [],
    "panels": [
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "datasource": "$datasource",
        "fill": 1,
        "fillGradient": 0,
        "gridPos": {
            "h": 8,
            "w": 18,
            "x": 0,
            "y": 0
        },
        "hiddenSeries": false,
        "id": 12,
        "legend": {
            "alignAsTable": true,
            "avg": true,
            "current": true,
            "max": true,
            "min": false,
            "rightSide": true,
            "show": true,
            "total": false,
            "values": true
        },
        "lines": true,
        "linewidth": 2,
        "nullPointMode": "null",
        "options": {
            "dataLinks": []
        },
        "percentage": false,
        "pointradius": 2,
        "points": false,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "expr": "DCGM_FI_DEV_GPU_TEMP{instance=~\"$instance\", gpu=~\"$gpu\"}",
            "instant": false,
            "interval": "",
            "legendFormat": "GPU {{gpu}}",
            "refId": "A"
            }
        ],
        "thresholds": [],
        "timeFrom": null,
        "timeRegions": [],
        "timeShift": null,
        "title": "GPU Temperature",
        "tooltip": {
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "buckets": null,
            "mode": "time",
            "name": null,
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "celsius",
            "label": null,
            "logBase": 1,
            "max": null,
            "min": null,
            "show": true
            },
            {
            "format": "short",
            "label": null,
            "logBase": 1,
            "max": null,
            "min": null,
            "show": true
            }
        ],
        "yaxis": {
            "align": false,
            "alignLevel": null
        }
        },
        {
        "datasource": "$datasource",
        "gridPos": {
            "h": 8,
            "w": 6,
            "x": 18,
            "y": 0
        },
        "id": 14,
        "options": {
            "fieldOptions": {
            "calcs": [
                "mean"
            ],
            "defaults": {
                "color": {
                "mode": "thresholds"
                },
                "mappings": [],
                "max": 100,
                "min": 0,
                "thresholds": {
                "mode": "absolute",
                "steps": [
                    {
                    "color": "green",
                    "value": null
                    },
                    {
                    "color": "#EAB839",
                    "value": 83
                    },
                    {
                    "color": "red",
                    "value": 87
                    }
                ]
                },
                "unit": "celsius"
            },
            "overrides": [],
            "values": false
            },
            "orientation": "auto",
            "showThresholdLabels": false,
            "showThresholdMarkers": true
        },
        "pluginVersion": "6.7.3",
        "targets": [
            {
            "expr": "avg(DCGM_FI_DEV_GPU_TEMP{instance=~\"$instance\", gpu=~\"$gpu\"})",
            "interval": "",
            "legendFormat": "",
            "refId": "A"
            }
        ],
        "timeFrom": null,
        "timeShift": null,
        "title": "GPU Avg. Temp",
        "type": "gauge"
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "datasource": "$datasource",
        "fill": 1,
        "fillGradient": 0,
        "gridPos": {
            "h": 8,
            "w": 18,
            "x": 0,
            "y": 8
        },
        "hiddenSeries": false,
        "id": 10,
        "legend": {
            "alignAsTable": true,
            "avg": true,
            "current": true,
            "max": true,
            "min": false,
            "rightSide": true,
            "show": true,
            "total": false,
            "values": true
        },
        "lines": true,
        "linewidth": 2,
        "nullPointMode": "null",
        "options": {
            "dataLinks": []
        },
        "percentage": false,
        "pluginVersion": "6.5.2",
        "pointradius": 2,
        "points": false,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "expr": "DCGM_FI_DEV_POWER_USAGE{instance=~\"$instance\", gpu=~\"$gpu\"}",
            "interval": "",
            "legendFormat": "GPU {{gpu}}",
            "refId": "A"
            }
        ],
        "thresholds": [],
        "timeFrom": null,
        "timeRegions": [],
        "timeShift": null,
        "title": "GPU Power Usage",
        "tooltip": {
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "buckets": null,
            "mode": "time",
            "name": null,
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "watt",
            "label": null,
            "logBase": 1,
            "max": null,
            "min": null,
            "show": true
            },
            {
            "format": "short",
            "label": null,
            "logBase": 1,
            "max": null,
            "min": null,
            "show": true
            }
        ],
        "yaxis": {
            "align": false,
            "alignLevel": null
        }
        },
        {
        "cacheTimeout": null,
        "datasource": "$datasource",
        "gridPos": {
            "h": 8,
            "w": 6,
            "x": 18,
            "y": 8
        },
        "id": 16,
        "links": [],
        "options": {
            "fieldOptions": {
            "calcs": [
                "sum"
            ],
            "defaults": {
                "color": {
                "mode": "thresholds"
                },
                "mappings": [],
                "max": 2400,
                "min": 0,
                "nullValueMode": "connected",
                "thresholds": {
                "mode": "absolute",
                "steps": [
                    {
                    "color": "green",
                    "value": null
                    },
                    {
                    "color": "#EAB839",
                    "value": 1800
                    },
                    {
                    "color": "red",
                    "value": 2200
                    }
                ]
                },
                "unit": "watt"
            },
            "overrides": [],
            "values": false
            },
            "orientation": "horizontal",
            "showThresholdLabels": false,
            "showThresholdMarkers": true
        },
        "pluginVersion": "6.7.3",
        "targets": [
            {
            "expr": "sum(DCGM_FI_DEV_POWER_USAGE{instance=~\"$instance\", gpu=~\"$gpu\"})",
            "instant": true,
            "interval": "",
            "legendFormat": "",
            "range": false,
            "refId": "A"
            }
        ],
        "timeFrom": null,
        "timeShift": null,
        "title": "GPU Power Total",
        "type": "gauge"
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "datasource": "$datasource",
        "fill": 1,
        "fillGradient": 0,
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 0,
            "y": 16
        },
        "hiddenSeries": false,
        "id": 2,
        "interval": "",
        "legend": {
            "alignAsTable": true,
            "avg": true,
            "current": true,
            "max": true,
            "min": false,
            "rightSide": true,
            "show": true,
            "sideWidth": null,
            "total": false,
            "values": true
        },
        "lines": true,
        "linewidth": 2,
        "nullPointMode": "null",
        "options": {
            "dataLinks": []
        },
        "percentage": false,
        "pointradius": 2,
        "points": false,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "expr": "DCGM_FI_DEV_SM_CLOCK{instance=~\"$instance\", gpu=~\"$gpu\"} * 1000000",
            "format": "time_series",
            "interval": "",
            "intervalFactor": 1,
            "legendFormat": "GPU {{gpu}}",
            "refId": "A"
            }
        ],
        "thresholds": [],
        "timeFrom": null,
        "timeRegions": [],
        "timeShift": null,
        "title": "GPU SM Clocks",
        "tooltip": {
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "buckets": null,
            "mode": "time",
            "name": null,
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "decimals": null,
            "format": "hertz",
            "label": "",
            "logBase": 1,
            "max": null,
            "min": null,
            "show": true
            },
            {
            "format": "short",
            "label": null,
            "logBase": 1,
            "max": null,
            "min": null,
            "show": true
            }
        ],
        "yaxis": {
            "align": false,
            "alignLevel": null
        }
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "datasource": "$datasource",
        "fill": 1,
        "fillGradient": 0,
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 0,
            "y": 24
        },
        "hiddenSeries": false,
        "id": 6,
        "legend": {
            "alignAsTable": true,
            "avg": true,
            "current": true,
            "max": true,
            "min": false,
            "rightSide": true,
            "show": true,
            "total": false,
            "values": true
        },
        "lines": true,
        "linewidth": 2,
        "nullPointMode": "null",
        "options": {
            "dataLinks": []
        },
        "percentage": false,
        "pointradius": 2,
        "points": false,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "expr": "DCGM_FI_DEV_GPU_UTIL{instance=~\"$instance\", gpu=~\"$gpu\"}",
            "interval": "",
            "legendFormat": "GPU {{gpu}}",
            "refId": "A"
            }
        ],
        "thresholds": [],
        "timeFrom": null,
        "timeRegions": [],
        "timeShift": null,
        "title": "GPU Utilization",
        "tooltip": {
            "shared": true,
            "sort": 0,
            "value_type": "cumulative"
        },
        "type": "graph",
        "xaxis": {
            "buckets": null,
            "mode": "time",
            "name": null,
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "percent",
            "label": null,
            "logBase": 1,
            "max": "100",
            "min": "0",
            "show": true
            },
            {
            "format": "short",
            "label": null,
            "logBase": 1,
            "max": null,
            "min": null,
            "show": true
            }
        ],
        "yaxis": {
            "align": false,
            "alignLevel": null
        }
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "datasource": "$datasource",
        "fill": 1,
        "fillGradient": 0,
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 0,
            "y": 32
        },
        "hiddenSeries": false,
        "id": 18,
        "legend": {
            "alignAsTable": true,
            "avg": true,
            "current": true,
            "max": true,
            "min": false,
            "rightSide": true,
            "show": true,
            "total": false,
            "values": true
        },
        "lines": true,
        "linewidth": 2,
        "nullPointMode": "null",
        "options": {
            "dataLinks": []
        },
        "percentage": false,
        "pointradius": 2,
        "points": false,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "expr": "DCGM_FI_DEV_FB_USED{instance=~\"$instance\", gpu=~\"$gpu\"}",
            "interval": "",
            "legendFormat": "GPU {{gpu}}",
            "refId": "A"
            }
        ],
        "thresholds": [],
        "timeFrom": null,
        "timeRegions": [],
        "timeShift": null,
        "title": "GPU Framebuffer Mem Used",
        "tooltip": {
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "buckets": null,
            "mode": "time",
            "name": null,
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "decmbytes",
            "label": null,
            "logBase": 1,
            "max": null,
            "min": null,
            "show": true
            },
            {
            "format": "short",
            "label": null,
            "logBase": 1,
            "max": null,
            "min": null,
            "show": true
            }
        ],
        "yaxis": {
            "align": false,
            "alignLevel": null
        }
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "datasource": "$datasource",
        "fill": 1,
        "fillGradient": 0,
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 0,
            "y": 24
        },
        "hiddenSeries": false,
        "id": 4,
        "legend": {
            "alignAsTable": true,
            "avg": true,
            "current": true,
            "max": true,
            "min": false,
            "rightSide": true,
            "show": true,
            "total": false,
            "values": true
        },
        "lines": true,
        "linewidth": 2,
        "nullPointMode": "null",
        "options": {
            "dataLinks": []
        },
        "percentage": false,
        "pointradius": 2,
        "points": false,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "expr": "DCGM_FI_PROF_PIPE_TENSOR_ACTIVE{instance=~\"$instance\", gpu=~\"$gpu\"}",
            "interval": "",
            "legendFormat": "GPU {{gpu}}",
            "refId": "A"
            }
        ],
        "thresholds": [],
        "timeFrom": null,
        "timeRegions": [],
        "timeShift": null,
        "title": "Tensor Core Utilization",
        "tooltip": {
            "shared": true,
            "sort": 0,
            "value_type": "cumulative"
        },
        "type": "graph",
        "xaxis": {
            "buckets": null,
            "mode": "time",
            "name": null,
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "percentunit",
            "label": null,
            "logBase": 1,
            "max": "1",
            "min": "0",
            "show": true
            },
            {
            "format": "short",
            "label": null,
            "logBase": 1,
            "max": null,
            "min": null,
            "show": true
            }
        ],
        "yaxis": {
            "align": false,
            "alignLevel": null
        }
        }
    ],
    "refresh": false,
    "schemaVersion": 22,
    "style": "dark",
    "tags": [],
    "templating": {
        "list": [
        {
            "current": {
            "selected": true,
            "text": "Prometheus",
            "value": "Prometheus"
            },
            "hide": 0,
            "includeAll": false,
            "multi": false,
            "name": "datasource",
            "options": [],
            "query": "prometheus",
            "queryValue": "",
            "refresh": 1,
            "regex": "",
            "skipUrlSync": false,
            "type": "datasource"
        },
        {
            "allValue": null,
            "current": {},
            "datasource": "$datasource",
            "definition": "label_values(DCGM_FI_DEV_GPU_TEMP, instance)",
            "hide": 0,
            "includeAll": true,
            "index": -1,
            "label": null,
            "multi": true,
            "name": "instance",
            "options": [],
            "query": "label_values(DCGM_FI_DEV_GPU_TEMP, instance)",
            "refresh": 1,
            "regex": "",
            "skipUrlSync": false,
            "sort": 1,
            "tagValuesQuery": "",
            "tags": [],
            "tagsQuery": "",
            "type": "query",
            "useTags": false
        },
        {
            "allValue": null,
            "current": {},
            "datasource": "$datasource",
            "definition": "label_values(DCGM_FI_DEV_GPU_TEMP, gpu)",
            "hide": 0,
            "includeAll": true,
            "index": -1,
            "label": null,
            "multi": true,
            "name": "gpu",
            "options": [],
            "query": "label_values(DCGM_FI_DEV_GPU_TEMP, gpu)",
            "refresh": 1,
            "regex": "",
            "skipUrlSync": false,
            "sort": 1,
            "tagValuesQuery": "",
            "tags": [],
            "tagsQuery": "",
            "type": "query",
            "useTags": false
        }
        ]
    },
    "time": {
        "from": "now-15m",
        "to": "now"
    },
    "timepicker": {
        "refresh_intervals": [
        "5s",
        "10s",
        "30s",
        "1m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "1d"
        ]
    },
    "timezone": "",
    "title": "NVIDIA DCGM Exporter Dashboard",
    "uid": "Oxed_c6Wz",
    "variables": {
        "list": []
    },
    "version": 1
    }
"""
named_process_dashboard=r"""
 {
    "annotations": {
        "list": [
        {
            "builtIn": 1,
            "datasource": "-- Grafana --",
            "enable": true,
            "hide": true,
            "iconColor": "rgba(0, 211, 255, 1)",
            "name": "Annotations & Alerts",
            "target": {
            "limit": 100,
            "matchAny": false,
            "tags": [],
            "type": "dashboard"
            },
            "type": "dashboard"
        }
        ]
    },
    "description": "Process metrics exported by https://github.com/ncabatoff/process-exporter.",
    "editable": true,
    "fiscalYearStartMonth": 0,
    "gnetId": 249,
    "graphTooltip": 1,
    "id": 4,
    "iteration": 1752490035433,
    "links": [
        {
        "asDropdown": true,
        "icon": "external link",
        "includeVars": true,
        "keepTime": true,
        "tags": [
            "OS"
        ],
        "title": "OS",
        "type": "dashboards"
        }
    ],
    "liveNow": false,
    "panels": [
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "editable": true,
        "error": false,
        "fill": 1,
        "fillGradient": 0,
        "grid": {},
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 0,
            "y": 0
        },
        "hiddenSeries": false,
        "id": 1,
        "isNew": true,
        "legend": {
            "avg": false,
            "current": false,
            "max": false,
            "min": false,
            "show": true,
            "total": false,
            "values": false
        },
        "lines": true,
        "linewidth": 2,
        "links": [],
        "nullPointMode": "null",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 5,
        "points": false,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "exemplar": true,
            "expr": "sum(namedprocess_namegroup_num_procs{groupname=~\"$processes\"}) without (mode)",
            "interval": "",
            "intervalFactor": 2,
            "legendFormat": "{{instance}}-{{groupname}}",
            "metric": "process_namegroup_num_procs",
            "refId": "A",
            "step": 10
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "num processes",
        "tooltip": {
            "msResolution": false,
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "short",
            "logBase": 1,
            "show": true
            },
            {
            "format": "short",
            "logBase": 1,
            "show": true
            }
        ],
        "yaxis": {
            "align": false
        }
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "editable": true,
        "error": false,
        "fill": 1,
        "fillGradient": 0,
        "grid": {},
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 12,
            "y": 0
        },
        "hiddenSeries": false,
        "id": 2,
        "isNew": true,
        "legend": {
            "avg": false,
            "current": false,
            "max": false,
            "min": false,
            "show": true,
            "total": false,
            "values": false
        },
        "lines": true,
        "linewidth": 2,
        "links": [],
        "nullPointMode": "null",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 5,
        "points": false,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "exemplar": true,
            "expr": "sum(rate(namedprocess_namegroup_cpu_seconds_total{groupname=~\"$processes\"}[$interval] )) without (mode)",
            "hide": false,
            "interval": "",
            "intervalFactor": 2,
            "legendFormat": "{{instance}}-{{groupname}}",
            "metric": "process_namegroup_cpu_seconds_total",
            "refId": "A",
            "step": 10
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "cpu",
        "tooltip": {
            "msResolution": false,
            "shared": true,
            "sort": 0,
            "value_type": "cumulative"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "s",
            "logBase": 1,
            "min": 0,
            "show": true
            },
            {
            "format": "short",
            "logBase": 1,
            "show": true
            }
        ],
        "yaxis": {
            "align": false
        }
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "editable": true,
        "error": false,
        "fill": 1,
        "fillGradient": 0,
        "grid": {},
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 0,
            "y": 7
        },
        "hiddenSeries": false,
        "id": 3,
        "isNew": true,
        "legend": {
            "avg": false,
            "current": false,
            "max": false,
            "min": false,
            "show": true,
            "total": false,
            "values": false
        },
        "lines": true,
        "linewidth": 2,
        "links": [],
        "nullPointMode": "null",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 5,
        "points": false,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "exemplar": true,
            "expr": "rate(namedprocess_namegroup_read_bytes_total{groupname=~\"$processes\"}[$interval])",
            "interval": "",
            "intervalFactor": 2,
            "legendFormat": "{{instance}}-{{groupname}}",
            "metric": "namedprocess_namegroup_read_bytes_total",
            "refId": "A",
            "step": 10
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "read bytes",
        "tooltip": {
            "msResolution": false,
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "Bps",
            "logBase": 1,
            "min": 0,
            "show": true
            },
            {
            "format": "short",
            "logBase": 1,
            "show": true
            }
        ],
        "yaxis": {
            "align": false
        }
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "editable": true,
        "error": false,
        "fill": 1,
        "fillGradient": 0,
        "grid": {},
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 12,
            "y": 7
        },
        "hiddenSeries": false,
        "id": 4,
        "isNew": true,
        "legend": {
            "avg": false,
            "current": false,
            "max": false,
            "min": false,
            "show": true,
            "total": false,
            "values": false
        },
        "lines": true,
        "linewidth": 2,
        "links": [],
        "nullPointMode": "null",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 5,
        "points": false,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "exemplar": true,
            "expr": "rate(namedprocess_namegroup_write_bytes_total{groupname=~\"$processes\"}[$interval])",
            "interval": "",
            "intervalFactor": 2,
            "legendFormat": "{{instance}}-{{groupname}}",
            "metric": "namedprocess_namegroup_read_bytes_total",
            "refId": "A",
            "step": 10
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "write bytes",
        "tooltip": {
            "msResolution": false,
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "Bps",
            "logBase": 1,
            "min": 0,
            "show": true
            },
            {
            "format": "short",
            "logBase": 1,
            "show": true
            }
        ],
        "yaxis": {
            "align": false
        }
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "editable": true,
        "error": false,
        "fill": 1,
        "fillGradient": 0,
        "grid": {},
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 0,
            "y": 14
        },
        "hiddenSeries": false,
        "id": 5,
        "isNew": true,
        "legend": {
            "avg": false,
            "current": false,
            "max": false,
            "min": false,
            "show": true,
            "total": false,
            "values": false
        },
        "lines": true,
        "linewidth": 2,
        "links": [],
        "nullPointMode": "null",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 5,
        "points": false,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "exemplar": true,
            "expr": "sum(namedprocess_namegroup_memory_bytes{groupname=~\"$processes\", memtype=\"resident\"}) without (mode)",
            "interval": "",
            "intervalFactor": 2,
            "legendFormat": "{{instance}}-{{groupname}}",
            "metric": "namedprocess_namegroup_memory_bytes",
            "refId": "A",
            "step": 10
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "resident memory",
        "tooltip": {
            "msResolution": false,
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "bytes",
            "logBase": 1,
            "min": 0,
            "show": true
            },
            {
            "format": "short",
            "logBase": 1,
            "show": true
            }
        ],
        "yaxis": {
            "align": false
        }
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "editable": true,
        "error": false,
        "fill": 1,
        "fillGradient": 0,
        "grid": {},
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 12,
            "y": 14
        },
        "hiddenSeries": false,
        "id": 6,
        "isNew": true,
        "legend": {
            "avg": false,
            "current": false,
            "max": false,
            "min": false,
            "show": true,
            "total": false,
            "values": false
        },
        "lines": true,
        "linewidth": 2,
        "links": [],
        "nullPointMode": "null",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 5,
        "points": false,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "exemplar": true,
            "expr": "sum(namedprocess_namegroup_memory_bytes{groupname=~\"$processes\", memtype=\"virtual\"}) without (mode)",
            "interval": "",
            "intervalFactor": 2,
            "legendFormat": "{{instance}}-{{groupname}}",
            "metric": "namedprocess_namegroup_memory_bytes",
            "refId": "A",
            "step": 10
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "virtual memory",
        "tooltip": {
            "msResolution": false,
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "bytes",
            "logBase": 1,
            "min": 0,
            "show": true
            },
            {
            "format": "short",
            "logBase": 1,
            "show": true
            }
        ],
        "yaxis": {
            "align": false
        }
        }
    ],
    "refresh": "10s",
    "schemaVersion": 35,
    "style": "dark",
    "tags": [
        "OS"
    ],
    "templating": {
        "list": [
        {
            "auto": false,
            "auto_count": 30,
            "auto_min": "10s",
            "current": {
            "selected": false,
            "text": "10m",
            "value": "10m"
            },
            "hide": 0,
            "includeAll": false,
            "multi": false,
            "name": "interval",
            "options": [
            {
                "selected": false,
                "text": "1m",
                "value": "1m"
            },
            {
                "selected": false,
                "text": "5m",
                "value": "5m"
            },
            {
                "selected": true,
                "text": "10m",
                "value": "10m"
            },
            {
                "selected": false,
                "text": "30m",
                "value": "30m"
            },
            {
                "selected": false,
                "text": "1h",
                "value": "1h"
            },
            {
                "selected": false,
                "text": "6h",
                "value": "6h"
            },
            {
                "selected": false,
                "text": "12h",
                "value": "12h"
            },
            {
                "selected": false,
                "text": "1d",
                "value": "1d"
            },
            {
                "selected": false,
                "text": "7d",
                "value": "7d"
            },
            {
                "selected": false,
                "text": "14d",
                "value": "14d"
            },
            {
                "selected": false,
                "text": "30d",
                "value": "30d"
            }
            ],
            "query": "1m,5m,10m,30m,1h,6h,12h,1d,7d,14d,30d",
            "refresh": 0,
            "skipUrlSync": false,
            "type": "interval"
        },
        {
            "allValue": ".+",
            "current": {
            "selected": false,
            "text": "All",
            "value": "$__all"
            },
            "definition": "",
            "hide": 0,
            "includeAll": true,
            "multi": true,
            "name": "processes",
            "options": [],
            "query": {
            "query": "label_values(namedprocess_namegroup_cpu_seconds_total,groupname)",
            "refId": "Prometheus-processes-Variable-Query"
            },
            "refresh": 1,
            "regex": "",
            "skipUrlSync": false,
            "sort": 0,
            "type": "query"
        }
        ]
    },
    "time": {
        "from": "now-1h",
        "to": "now"
    },
    "timepicker": {
        "refresh_intervals": [
        "5s",
        "10s",
        "30s",
        "1m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "1d"
        ],
        "time_options": [
        "5m",
        "15m",
        "1h",
        "6h",
        "12h",
        "24h",
        "2d",
        "7d",
        "30d"
        ]
    },
    "timezone": "browser",
    "title": "Named processes",
    "uid": "oqGKqUYnk",
    "version": 5,
    "weekStart": ""
    }
"""
node_single_server_dashboard=r"""
 {
    "annotations": {
        "list": [
        {
            "builtIn": 1,
            "datasource": "-- Grafana --",
            "enable": true,
            "hide": true,
            "iconColor": "rgba(0, 211, 255, 1)",
            "name": "Annotations & Alerts",
            "target": {
            "limit": 100,
            "matchAny": false,
            "tags": [],
            "type": "dashboard"
            },
            "type": "dashboard"
        }
        ]
    },
    "description": "Dashboard to get an overview of one server",
    "editable": true,
    "fiscalYearStartMonth": 0,
    "gnetId": 22,
    "graphTooltip": 0,
    "id": 3,
    "iteration": 1752489703146,
    "links": [],
    "liveNow": false,
    "panels": [
        {
        "fieldConfig": {
            "defaults": {
            "decimals": 1,
            "displayName": "天",
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "#e9edf1",
                    "value": null
                }
                ]
            },
            "unit": "short"
            },
            "overrides": [
            {
                "matcher": {
                "id": "byName",
                "options": "  运行天数"
                },
                "properties": [
                {
                    "id": "unit",
                    "value": "short"
                },
                {
                    "id": "displayName",
                    "value": "天"
                }
                ]
            }
            ]
        },
        "gridPos": {
            "h": 4,
            "w": 4,
            "x": 0,
            "y": 0
        },
        "id": 17,
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "center",
            "orientation": "vertical",
            "reduceOptions": {
            "calcs": [
                "lastNotNull"
            ],
            "fields": "",
            "values": false
            },
            "text": {
            "titleSize": 5
            },
            "textMode": "auto"
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": false,
            "expr": "( time() -avg( node_boot_time_seconds{instance=~\"$server\",job=~\"node|node-exporter\"} )) / 3600/24 ",
            "format": "heatmap",
            "interval": "",
            "legendFormat": "  运行天数",
            "refId": "A"
            },
            {
            "exemplar": false,
            "expr": "node_boot_time_seconds{instance=~\"$server\"} * 1000",
            "format": "heatmap",
            "hide": true,
            "interval": "",
            "legendFormat": "启动时间",
            "refId": "B"
            }
        ],
        "title": "系统运行天数",
        "type": "stat"
        },
        {
        "gridPos": {
            "h": 4,
            "w": 4,
            "x": 4,
            "y": 0
        },
        "id": 32,
        "options": {
            "bgColor": "transparent",
            "clockType": "24 hour",
            "countdownSettings": {
            "endCountdownTime": "2022-04-07T15:59:51+08:00",
            "endText": "00:00:00"
            },
            "countupSettings": {
            "beginCountupTime": "2022-04-07T15:59:51+08:00",
            "beginText": "00:00:00"
            },
            "dateSettings": {
            "dateFormat": "YYYY-MM-DD",
            "fontSize": "35px",
            "fontWeight": "normal",
            "locale": "",
            "showDate": true
            },
            "mode": "time",
            "refresh": "sec",
            "timeSettings": {
            "fontSize": "45px",
            "fontWeight": "normal"
            },
            "timezone": "Asia/Shanghai",
            "timezoneSettings": {
            "fontSize": "12px",
            "fontWeight": "normal",
            "showTimezone": false,
            "zoneFormat": "offsetAbbv"
            }
        },
        "pluginVersion": "1.3.0",
        "targets": [
            {
            "exemplar": false,
            "expr": "time() * 1000",
            "instant": true,
            "interval": "",
            "legendFormat": "",
            "refId": "A"
            }
        ],
        "title": "本地时间",
        "type": "grafana-clock-panel"
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "thresholds"
            },
            "decimals": 1,
            "mappings": [],
            "max": 100,
            "min": 0,
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            },
            "unit": "percent"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 6,
            "w": 4,
            "x": 8,
            "y": 0
        },
        "id": 26,
        "options": {
            "orientation": "auto",
            "reduceOptions": {
            "calcs": [
                "lastNotNull"
            ],
            "fields": "",
            "values": false
            },
            "showThresholdLabels": false,
            "showThresholdMarkers": true
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": true,
            "expr": "100 * (1 - avg (irate(node_cpu_seconds_total{mode='idle', instance=~\"$server\"}[5m]))by(instance))",
            "interval": "",
            "legendFormat": "",
            "refId": "A"
            }
        ],
        "title": "cpu使用率",
        "type": "gauge"
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "thresholds"
            },
            "mappings": [
                {
                "options": {
                    "match": "null",
                    "result": {
                    "text": "N/A"
                    }
                },
                "type": "special"
                }
            ],
            "max": 100,
            "min": 0,
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "#EAB839",
                    "value": 70
                },
                {
                    "color": "red",
                    "value": 90
                }
                ]
            },
            "unit": "percent"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 6,
            "w": 6,
            "x": 12,
            "y": 0
        },
        "id": 5,
        "links": [],
        "maxDataPoints": 100,
        "options": {
            "orientation": "horizontal",
            "reduceOptions": {
            "calcs": [
                "mean"
            ],
            "fields": "",
            "values": false
            },
            "showThresholdLabels": false,
            "showThresholdMarkers": true
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": true,
            "expr": "(1- (node_memory_MemFree_bytes{instance=~\"$server\",job=~\"node-exporter\"} / node_memory_MemTotal_bytes{instance=~\"$server\",job=~\"node-exporter\"}))* 100",
            "hide": false,
            "interval": "",
            "intervalFactor": 2,
            "legendFormat": "",
            "refId": "A",
            "step": 60,
            "target": ""
            },
            {
            "exemplar": true,
            "expr": " (node_memory_MemFree_bytes{instance=~\"$server\",job=~\"node-exporter\"}",
            "hide": true,
            "interval": "",
            "legendFormat": "",
            "refId": "B"
            }
        ],
        "title": "内存使用率",
        "type": "gauge"
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "thresholds"
            },
            "mappings": [
                {
                "options": {
                    "match": "null",
                    "result": {
                    "text": "N/A"
                    }
                },
                "type": "special"
                }
            ],
            "max": 100,
            "min": 0,
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "#EAB839",
                    "value": 65
                },
                {
                    "color": "red",
                    "value": 75
                }
                ]
            },
            "unit": "percent"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 6,
            "w": 6,
            "x": 18,
            "y": 0
        },
        "id": 7,
        "links": [],
        "maxDataPoints": 100,
        "options": {
            "orientation": "horizontal",
            "reduceOptions": {
            "calcs": [
                "lastNotNull"
            ],
            "fields": "",
            "values": false
            },
            "showThresholdLabels": false,
            "showThresholdMarkers": true
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": true,
            "expr": "min((node_filesystem_size_bytes{mountpoint=\"/\",instance=~\"$server\"} - node_filesystem_free_bytes{mountpoint=\"/\",instance=~\"$server\"} )/ node_filesystem_size_bytes{mountpoint=\"/\",instance=~\"$server\"}) * 100",
            "interval": "",
            "intervalFactor": 2,
            "legendFormat": "",
            "refId": "A",
            "step": 60,
            "target": ""
            }
        ],
        "title": "根分区磁盘使用率",
        "type": "gauge"
        },
        {
        "fieldConfig": {
            "defaults": {
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "#e9edf1",
                    "value": null
                }
                ]
            },
            "unit": "short"
            },
            "overrides": [
            {
                "matcher": {
                "id": "byName",
                "options": "启动时间"
                },
                "properties": [
                {
                    "id": "unit",
                    "value": "dateTimeAsLocal"
                }
                ]
            },
            {
                "matcher": {
                "id": "byName",
                "options": "在线"
                },
                "properties": [
                {
                    "id": "unit",
                    "value": "short"
                },
                {
                    "id": "noValue",
                    "value": "下线"
                },
                {
                    "id": "displayName",
                    "value": "在线"
                }
                ]
            }
            ]
        },
        "gridPos": {
            "h": 5,
            "w": 4,
            "x": 0,
            "y": 4
        },
        "id": 23,
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "center",
            "orientation": "vertical",
            "reduceOptions": {
            "calcs": [
                "lastNotNull"
            ],
            "fields": "",
            "values": false
            },
            "textMode": "auto"
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": false,
            "expr": "ceil(( time() - node_boot_time_seconds{instance=~\"$server\"} ) / 3600 /24)",
            "format": "heatmap",
            "hide": true,
            "interval": "",
            "legendFormat": "  运行天数",
            "refId": "A"
            },
            {
            "exemplar": false,
            "expr": "avg(node_boot_time_seconds{instance=~\"$server\",job=~\"node|node-exporter\"}) * 1000",
            "format": "heatmap",
            "hide": false,
            "interval": "",
            "legendFormat": "启动时间",
            "refId": "B"
            }
        ],
        "title": "上次开机时间",
        "type": "stat"
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "thresholds"
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "#f6f8f6",
                    "value": null
                }
                ]
            },
            "unit": "dateTimeFromNow"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 5,
            "w": 4,
            "x": 4,
            "y": 4
        },
        "id": 33,
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "center",
            "orientation": "horizontal",
            "reduceOptions": {
            "calcs": [
                "lastNotNull"
            ],
            "fields": "",
            "values": false
            },
            "textMode": "auto"
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": false,
            "expr": "node_boot_time_seconds{instance=~\"$server\"} * 1000",
            "instant": true,
            "interval": "",
            "legendFormat": "",
            "refId": "A"
            }
        ],
        "title": "开机时间",
        "type": "stat"
        },
        {
        "fieldConfig": {
            "defaults": {
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "#f9f9f9",
                    "value": null
                }
                ]
            },
            "unit": "decgbytes"
            },
            "overrides": [
            {
                "matcher": {
                "id": "byName",
                "options": "cpu核数"
                },
                "properties": [
                {
                    "id": "unit",
                    "value": "short"
                }
                ]
            }
            ]
        },
        "gridPos": {
            "h": 3,
            "w": 4,
            "x": 8,
            "y": 6
        },
        "id": 12,
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "center",
            "orientation": "auto",
            "reduceOptions": {
            "calcs": [
                "lastNotNull"
            ],
            "fields": "",
            "values": false
            },
            "text": {
            "valueSize": 46
            },
            "textMode": "auto"
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": true,
            "expr": "count(sum(node_cpu_seconds_total{instance=~\"$server\"})by (cpu))",
            "hide": false,
            "interval": "",
            "legendFormat": "cpu核数",
            "refId": "C"
            }
        ],
        "title": "cpu核数",
        "type": "stat"
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "thresholds"
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "#f6f6ff",
                    "value": null
                }
                ]
            },
            "unit": "decgbytes"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 3,
            "w": 3,
            "x": 12,
            "y": 6
        },
        "id": 19,
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "center",
            "orientation": "auto",
            "reduceOptions": {
            "calcs": [
                "lastNotNull"
            ],
            "fields": "",
            "values": false
            },
            "textMode": "auto"
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": false,
            "expr": "avg(node_memory_MemTotal_bytes{instance=~\"$server\",job=~\"node|node-exporter\"}) / 1024 /1024 / 1024",
            "interval": "",
            "legendFormat": "",
            "refId": "A"
            }
        ],
        "title": "总内存容量",
        "type": "stat"
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "thresholds"
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "text",
                    "value": null
                }
                ]
            },
            "unit": "decgbytes"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 3,
            "w": 3,
            "x": 15,
            "y": 6
        },
        "id": 20,
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "center",
            "orientation": "auto",
            "reduceOptions": {
            "calcs": [
                "lastNotNull"
            ],
            "fields": "",
            "values": false
            },
            "textMode": "auto"
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": false,
            "expr": "avg(node_memory_MemFree_bytes{instance=~\"$server\",job=~\"node|node-exporter\"}) / 1024 /1024 / 1024",
            "interval": "",
            "legendFormat": "",
            "refId": "A"
            }
        ],
        "title": "剩余内存",
        "type": "stat"
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "thresholds"
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "#b7b7ed",
                    "value": null
                }
                ]
            },
            "unit": "decgbytes"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 3,
            "w": 3,
            "x": 18,
            "y": 6
        },
        "id": 24,
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "center",
            "orientation": "auto",
            "reduceOptions": {
            "calcs": [
                "lastNotNull"
            ],
            "fields": "",
            "values": false
            },
            "textMode": "auto"
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": false,
            "expr": "avg(node_filesystem_size_bytes{mountpoint=\"/\",instance=~\"$server\",job=~\"node|node-exporter\"}) /1024/1024/1024",
            "interval": "",
            "legendFormat": "",
            "refId": "A"
            }
        ],
        "title": "根文件系统容量",
        "type": "stat"
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "thresholds"
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "text",
                    "value": null
                }
                ]
            },
            "unit": "decgbytes"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 3,
            "w": 3,
            "x": 21,
            "y": 6
        },
        "id": 27,
        "options": {
            "colorMode": "background",
            "graphMode": "none",
            "justifyMode": "center",
            "orientation": "auto",
            "reduceOptions": {
            "calcs": [
                "lastNotNull"
            ],
            "fields": "",
            "values": false
            },
            "textMode": "auto"
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": false,
            "expr": "avg(node_filesystem_free_bytes{mountpoint=\"/\",instance=~\"$server\",job=~\"node|node-exporter\"}) /1024/1024/1024",
            "interval": "",
            "legendFormat": "",
            "refId": "A"
            }
        ],
        "title": "剩余容量",
        "type": "stat"
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisLabel": "",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "drawStyle": "line",
                "fillOpacity": 10,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "lineInterpolation": "linear",
                "lineWidth": 2,
                "pointSize": 5,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "never",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            },
            "unit": "percentunit"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 0,
            "y": 9
        },
        "id": 9,
        "links": [],
        "options": {
            "legend": {
            "calcs": [
                "mean",
                "lastNotNull",
                "max"
            ],
            "displayMode": "table",
            "placement": "right"
            },
            "tooltip": {
            "mode": "multi",
            "sort": "none"
            }
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": true,
            "expr": "node_load1{instance=~\"$server\"}",
            "interval": "",
            "intervalFactor": 4,
            "legendFormat": "load 1m",
            "refId": "A",
            "step": 8,
            "target": ""
            },
            {
            "expr": "node_load5{instance=~\"$server\"}",
            "intervalFactor": 4,
            "legendFormat": "load 5m",
            "refId": "B",
            "step": 8,
            "target": ""
            },
            {
            "expr": "node_load15{instance=~\"$server\"}",
            "intervalFactor": 4,
            "legendFormat": "load 15m",
            "refId": "C",
            "step": 8,
            "target": ""
            }
        ],
        "title": "系统负载",
        "type": "timeseries"
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisLabel": "cpu usage",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "drawStyle": "line",
                "fillOpacity": 10,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "lineInterpolation": "linear",
                "lineWidth": 2,
                "pointSize": 5,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "never",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "max": 100,
            "min": 0,
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            },
            "unit": "percent"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 12,
            "y": 9
        },
        "id": 3,
        "links": [],
        "options": {
            "legend": {
            "calcs": [],
            "displayMode": "list",
            "placement": "bottom"
            },
            "tooltip": {
            "mode": "multi",
            "sort": "none"
            }
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": true,
            "expr": "100 - (avg by (cpu) (irate(node_cpu_seconds_total{mode=\"idle\", instance=~\"$server\"}[5m])) * 100)",
            "hide": false,
            "interval": "",
            "intervalFactor": 10,
            "legendFormat": "{{cpu}}",
            "refId": "A",
            "step": 20
            }
        ],
        "title": "各cpu 使用率",
        "type": "timeseries"
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisLabel": "cpu usage",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "drawStyle": "line",
                "fillOpacity": 10,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "lineInterpolation": "linear",
                "lineWidth": 2,
                "pointSize": 5,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "never",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "max": 100,
            "min": 0,
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "#EAB839",
                    "value": 70
                },
                {
                    "color": "red",
                    "value": 85
                }
                ]
            },
            "unit": "percent"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 9,
            "w": 12,
            "x": 0,
            "y": 17
        },
        "id": 21,
        "links": [],
        "options": {
            "legend": {
            "calcs": [
                "max",
                "mean"
            ],
            "displayMode": "table",
            "placement": "bottom"
            },
            "tooltip": {
            "mode": "multi",
            "sort": "none"
            }
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": true,
            "expr": "100 * (1 - avg (irate(node_cpu_seconds_total{mode='idle', instance=~\"$server\"}[5m]))by(instance))",
            "hide": false,
            "interval": "",
            "intervalFactor": 10,
            "legendFormat": "",
            "refId": "A",
            "step": 20
            }
        ],
        "title": "节点总cpu使用率",
        "type": "timeseries"
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "thresholds"
            },
            "custom": {
                "axisLabel": "",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "drawStyle": "line",
                "fillOpacity": 0,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "lineInterpolation": "linear",
                "lineWidth": 2,
                "pointSize": 5,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "auto",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            },
            "unit": "percent"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 9,
            "w": 12,
            "x": 12,
            "y": 17
        },
        "id": 4,
        "links": [],
        "options": {
            "legend": {
            "calcs": [
                "lastNotNull",
                "max"
            ],
            "displayMode": "table",
            "placement": "bottom"
            },
            "tooltip": {
            "mode": "single",
            "sort": "none"
            }
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": true,
            "expr": "(1-  rate(node_memory_MemFree_bytes{instance=~\"$server\"})[1m]/rate(node_memory_MemTotal_bytes{instance=~\"$server\"}[1m])) * 100",
            "hide": true,
            "interval": "",
            "intervalFactor": 2,
            "legendFormat": "{{instance}}",
            "metric": "memo",
            "refId": "A",
            "step": 4,
            "target": ""
            },
            {
            "exemplar": true,
            "expr": "(node_memory_Active_bytes{instance=~\"$server\"}/node_memory_MemTotal_bytes{instance=~\"$server\"}) * 100",
            "hide": false,
            "interval": "",
            "legendFormat": "{{instance}}",
            "refId": "B"
            }
        ],
        "title": "历史内存使用率",
        "type": "timeseries"
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "editable": true,
        "error": false,
        "fill": 1,
        "fillGradient": 0,
        "grid": {},
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 0,
            "y": 26
        },
        "hiddenSeries": false,
        "id": 6,
        "isNew": true,
        "legend": {
            "avg": false,
            "current": false,
            "max": false,
            "min": false,
            "show": true,
            "total": false,
            "values": false
        },
        "lines": true,
        "linewidth": 2,
        "links": [],
        "nullPointMode": "connected",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 5,
        "points": false,
        "renderer": "flot",
        "seriesOverrides": [
            {
            "$$hashKey": "object:1195",
            "alias": "read",
            "yaxis": 1
            },
            {
            "$$hashKey": "object:1196",
            "alias": "{instance=\"172.17.0.1:9100\"}",
            "yaxis": 2
            },
            {
            "$$hashKey": "object:1197",
            "alias": "io time",
            "yaxis": 2
            }
        ],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "exemplar": true,
            "expr": "sum by (instance) (irate(node_disk_read_bytes_total{instance=~\"$server\"}[5m]))",
            "hide": false,
            "interval": "",
            "intervalFactor": 4,
            "legendFormat": "read",
            "refId": "A",
            "step": 8,
            "target": ""
            },
            {
            "exemplar": true,
            "expr": "sum by (instance) (irate(node_disk_written_bytes_total{instance=~\"$server\"}[5m]))",
            "interval": "",
            "intervalFactor": 4,
            "legendFormat": "written",
            "refId": "B",
            "step": 8
            },
            {
            "exemplar": true,
            "expr": "sum by (instance) (irate(node_disk_io_time_seconds_total{instance=~\"$server\"}[5m]))",
            "interval": "",
            "intervalFactor": 4,
            "legendFormat": "io time",
            "refId": "C",
            "step": 8
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "Disk usage",
        "tooltip": {
            "msResolution": false,
            "shared": true,
            "sort": 0,
            "value_type": "cumulative"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "bytes",
            "logBase": 1,
            "show": true
            },
            {
            "format": "ms",
            "logBase": 1,
            "show": true
            }
        ],
        "yaxis": {
            "align": false
        }
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "thresholds"
            },
            "custom": {
                "align": "auto",
                "displayMode": "auto",
                "filterable": false
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            }
            },
            "overrides": [
            {
                "matcher": {
                "id": "byName",
                "options": "mountpoint"
                },
                "properties": [
                {
                    "id": "displayName",
                    "value": "挂载点"
                }
                ]
            },
            {
                "matcher": {
                "id": "byName",
                "options": "device 1"
                },
                "properties": [
                {
                    "id": "displayName",
                    "value": "设备"
                }
                ]
            },
            {
                "matcher": {
                "id": "byName",
                "options": "fstype"
                },
                "properties": [
                {
                    "id": "displayName",
                    "value": "文件系统"
                }
                ]
            },
            {
                "matcher": {
                "id": "byName",
                "options": "Value #A"
                },
                "properties": [
                {
                    "id": "displayName",
                    "value": "剩余容量"
                },
                {
                    "id": "unit",
                    "value": "decgbytes"
                }
                ]
            },
            {
                "matcher": {
                "id": "byName",
                "options": "Value #C"
                },
                "properties": [
                {
                    "id": "unit",
                    "value": "decgbytes"
                },
                {
                    "id": "displayName",
                    "value": "总容量"
                }
                ]
            },
            {
                "matcher": {
                "id": "byName",
                "options": "Value #B / Value #C"
                },
                "properties": [
                {
                    "id": "displayName",
                    "value": "使用率"
                },
                {
                    "id": "unit",
                    "value": "percentunit"
                },
                {
                    "id": "custom.displayMode",
                    "value": "gradient-gauge"
                },
                {
                    "id": "thresholds",
                    "value": {
                    "mode": "absolute",
                    "steps": [
                        {
                        "color": "green",
                        "value": null
                        },
                        {
                        "color": "#EAB839",
                        "value": 0.7
                        },
                        {
                        "color": "red",
                        "value": 0.85
                        }
                    ]
                    }
                }
                ]
            }
            ]
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 12,
            "y": 26
        },
        "id": 30,
        "options": {
            "footer": {
            "fields": "",
            "reducer": [
                "sum"
            ],
            "show": false
            },
            "showHeader": true,
            "sortBy": [
            {
                "desc": false,
                "displayName": "Value #B"
            }
            ]
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": false,
            "expr": "node_filesystem_free_bytes{fstype!=\"tmpfs\",instance=~\"$server\"} / 1024 /1024 /1024",
            "format": "table",
            "hide": false,
            "instant": true,
            "interval": "",
            "legendFormat": "",
            "refId": "A"
            },
            {

            "exemplar": false,
            "expr": "(node_filesystem_size_bytes{fstype!=\"tmpfs\",instance=~\"$server\"} - node_filesystem_free_bytes{fstype!=\"tmpfs\",instance=~\"$server\"}) / 1024 / 1024 /1024",
            "format": "table",
            "hide": false,
            "instant": true,
            "interval": "",
            "legendFormat": "",
            "refId": "B"
            },
            {
            "exemplar": false,
            "expr": "node_filesystem_size_bytes{fstype!=\"tmpfs\",instance=~\"$server\"} / 1024 /1024 /1024",
            "format": "table",
            "hide": false,
            "instant": true,
            "interval": "",
            "legendFormat": "",
            "refId": "C"
            }
        ],
        "title": "磁盘使用率",
        "transformations": [
            {
            "id": "seriesToColumns",
            "options": {
                "byField": "mountpoint"
            }
            },
            {
            "id": "calculateField",
            "options": {
                "binary": {
                "left": "Value #B",
                "operator": "/",
                "reducer": "sum",
                "right": "Value #C"
                },
                "mode": "binary",
                "reduce": {
                "reducer": "sum"
                }
            }
            },
            {
            "id": "filterFieldsByName",
            "options": {
                "include": {
                "names": [
                    "mountpoint",
                    "device 1",
                    "fstype 1",
                    "Value #A",
                    "Value #C",
                    "Value #B / Value #C"
                ]
                }
            }
            }
        ],
        "type": "table"
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisLabel": "",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "drawStyle": "line",
                "fillOpacity": 10,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "lineInterpolation": "linear",
                "lineWidth": 2,
                "pointSize": 5,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "never",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            },
            "unit": "Bps"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 0,
            "y": 34
        },
        "id": 8,
        "links": [],
        "options": {
            "legend": {
            "calcs": [],
            "displayMode": "hidden",
            "placement": "bottom"
            },
            "tooltip": {
            "mode": "multi",
            "sort": "none"
            }
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": true,
            "expr": "irate(node_network_transmit_bytes_total{instance=~\"$server\",device!~\"lo\"}[5m])",
            "hide": false,
            "interval": "",
            "intervalFactor": 2,
            "legendFormat": "{{device}}",
            "refId": "A",
            "step": 2,
            "target": ""
            },
            {
            "exemplar": true,
            "expr": "irate(node_network_transmit_bytes_total{instance=~\"$server\",device!~\"lo\"}[5m])",
            "hide": true,
            "interval": "",
            "intervalFactor": 2,
            "legendFormat": "transmitted ",
            "refId": "B",
            "step": 2,
            "target": ""
            },
            {
            "exemplar": true,
            "expr": "node_network_transmit_bytes_total{instance=~\"$server\",device!~\"lo\"}",
            "hide": true,
            "interval": "",
            "intervalFactor": 2,
            "legendFormat": "transmitted ",
            "refId": "C",
            "step": 2,
            "target": ""
            }
        ],
        "title": "网络流出速率",
        "type": "timeseries"
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisLabel": "",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "drawStyle": "line",
                "fillOpacity": 10,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "lineInterpolation": "linear",
                "lineWidth": 2,
                "pointSize": 5,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "never",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            },
            "unit": "Bps"
            },
            "overrides": [
            {
                "matcher": {
                "id": "byName",
                "options": "transmitted"
                },
                "properties": [
                {
                    "id": "unit",
                    "value": "bytes"
                }
                ]
            }
            ]
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 12,
            "y": 34
        },
        "id": 10,
        "links": [],
        "options": {
            "legend": {
            "calcs": [],
            "displayMode": "hidden",
            "placement": "bottom"
            },
            "tooltip": {
            "mode": "multi",
            "sort": "none"
            }
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": true,
            "expr": "irate(node_network_receive_bytes_total{instance=~\"$server\",device!~\"lo\"}[5m])",
            "hide": false,
            "interval": "",
            "intervalFactor": 2,
            "legendFormat": "{{device}}",
            "refId": "A",
            "step": 2,
            "target": ""
            }
        ],
        "title": "网络流入速率",
        "type": "timeseries"
        }
    ],
    "refresh": false,
    "schemaVersion": 35,
    "style": "dark",
    "tags": [
        "prometheus"
    ],
    "templating": {
        "list": [
        {
            "current": {
            "selected": false,
            "text": "10.30.100.244:8081",
            "value": "10.30.100.244:8081"
            },
            "definition": "label_values(node_boot_time_seconds, instance)",
            "hide": 0,
            "includeAll": false,
            "multi": false,
            "name": "server",
            "options": [],
            "query": {
            "query": "label_values(node_boot_time_seconds, instance)",
            "refId": "StandardVariableQuery"
            },
            "refresh": 1,
            "regex": "",
            "skipUrlSync": false,
            "sort": 0,
            "type": "query"
        },
        {
            "filters": [],
            "hide": 0,
            "name": "Filters",
            "skipUrlSync": false,
            "type": "adhoc"
        }
        ]
    },
    "time": {
        "from": "now-1h",
        "to": "now"
    },
    "timepicker": {
        "refresh_intervals": [
        "5s",
        "10s",
        "30s",
        "1m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "1d"
        ],
        "time_options": [
        "5m",
        "15m",
        "1h",
        "6h",
        "12h",
        "24h",
        "2d",
        "7d",
        "30d"
        ]
    },
    "timezone": "browser",
    "title": "Node exporter single server",
    "uid": "qkWShPL7k",
    "version": 31,
    "weekStart": ""
    }                                                                
    """
system_process_metrics_dashboard=r"""
    {
    "annotations": {
        "list": [
        {
            "builtIn": 1,
            "datasource": "-- Grafana --",
            "enable": true,
            "hide": true,
            "iconColor": "rgba(0, 211, 255, 1)",
            "name": "Annotations & Alerts",
            "target": {
            "limit": 100,
            "matchAny": false,
            "tags": [],
            "type": "dashboard"
            },
            "type": "dashboard"
        }
        ]
    },
    "description": "Show Linux Process information as captured by \n https://github.com/ncabatoff/process-exporter  designed for PMM",
    "editable": true,
    "fiscalYearStartMonth": 0,
    "gnetId": 8378,
    "graphTooltip": 1,
    "id": 1,
    "iteration": 1752489881208,
    "links": [
        {
        "icon": "dashboard",
        "includeVars": false,
        "keepTime": true,
        "tags": [
            "QAN"
        ],
        "targetBlank": false,
        "title": "Query Analytics",
        "type": "link",
        "url": "/graph/dashboard/db/_pmm-query-analytics"
        },
        {
        "asDropdown": true,
        "includeVars": false,
        "keepTime": true,
        "tags": [
            "OS"
        ],
        "targetBlank": false,
        "title": "OS",
        "type": "dashboards"
        },
        {
        "asDropdown": true,
        "includeVars": false,
        "keepTime": true,
        "tags": [
            "MySQL"
        ],
        "targetBlank": false,
        "title": "MySQL",
        "type": "dashboards"
        },
        {
        "asDropdown": true,
        "includeVars": false,
        "keepTime": true,
        "tags": [
            "MongoDB"
        ],
        "targetBlank": false,
        "title": "MongoDB",
        "type": "dashboards"
        },
        {
        "asDropdown": true,
        "includeVars": false,
        "keepTime": true,
        "tags": [
            "HA"
        ],
        "targetBlank": false,
        "title": "HA",
        "type": "dashboards"
        },
        {
        "asDropdown": true,
        "includeVars": false,
        "keepTime": true,
        "tags": [
            "Cloud"
        ],
        "targetBlank": false,
        "title": "Cloud",
        "type": "dashboards"
        },
        {
        "asDropdown": true,
        "includeVars": true,
        "keepTime": true,
        "tags": [
            "Insight"
        ],
        "targetBlank": false,
        "title": "Insight",
        "type": "dashboards"
        },
        {
        "asDropdown": true,
        "includeVars": false,
        "keepTime": true,
        "tags": [
            "PMM"
        ],
        "targetBlank": false,
        "title": "PMM",
        "type": "dashboards"
        }
    ],
    "liveNow": false,
    "panels": [
        {
        "gridPos": {
            "h": 2,
            "w": 24,
            "x": 0,
            "y": 0
        },
        "id": 16,
        "links": [],
        "options": {
            "content": "<h1><i><font color=#5991A7><b><center>Data for </font><font color=#e68a00>$host</font> <font color=#5991A7> with</font> </font><font color=#e68a00>$interval</font> <font color=#5991A7>resolution</center></b></font></i></h1>",
            "mode": "html"
        },
        "pluginVersion": "8.4.1",
        "type": "text"
        },
        {
        "collapsed": false,
        "gridPos": {
            "h": 1,
            "w": 24,
            "x": 0,
            "y": 2
        },
        "id": 31,
        "panels": [],
        "title": "Process CPU Usage",
        "type": "row"
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisLabel": "",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "drawStyle": "line",
                "fillOpacity": 20,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "lineInterpolation": "linear",
                "lineWidth": 2,
                "pointSize": 4,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "always",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "min": 0,
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            },
            "unit": "percentunit"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 0,
            "y": 3
        },
        "id": 2,
        "links": [],
        "options": {
            "legend": {
            "calcs": [
                "mean",
                "max",
                "min"
            ],
            "displayMode": "table",
            "placement": "right"
            },
            "tooltip": {
            "mode": "multi",
            "sort": "none"
            }
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": true,
            "expr": "topk(10,(rate(namedprocess_namegroup_cpu_seconds_total{groupname=~\"$processes\",instance=~\"$host\",mode=\"user\"}[$interval]) \n+\nrate(namedprocess_namegroup_cpu_seconds_total{groupname=~\"$processes\",instance=~\"$host\",mode=\"system\"}[$interval]))\nor \n(irate(namedprocess_namegroup_cpu_seconds_total{groupname=~\"$processes\",instance=~\"$host\",mode=\"user\"}[5m])\n+\nirate(namedprocess_namegroup_cpu_seconds_total{groupname=~\"$processes\",instance=~\"$host\",mode=\"system\"}[5m])))",
            "format": "time_series",
            "hide": true,
            "interval": "$interval",
            "intervalFactor": 1,
            "legendFormat": "{{groupname}}",
            "metric": "process_namegroup_cpu_seconds_total",
            "refId": "A",
            "step": 10
            },
            {
            "exemplar": true,
            "expr": "topk(10,sum ((rate(namedprocess_namegroup_cpu_seconds_total{groupname=~\".+\",instance=~\"10.30.100.82:9256\"}[$interval]) \r\nor\r\nirate(namedprocess_namegroup_cpu_seconds_total{groupname=~\".+\",instance=~\"10.30.100.82:9256\"}[5m])) ) without (mode))",
            "hide": true,
            "instant": false,
            "interval": "",
            "legendFormat": "{{groupname}}",
            "refId": "B"
            },
            {
            "exemplar": true,
            "expr": "sum(rate(namedprocess_namegroup_cpu_seconds_total{groupname=~\".+\",instance=~\"10.30.100.82:9256\"}[$interval])) without (mode)",
            "hide": true,
            "interval": "",
            "legendFormat": "",
            "refId": "C"
            }
        ],
        "title": "Top processes by Total CPU cores used",
        "type": "timeseries"
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "decimals": 2,
        "editable": true,
        "error": false,
        "fill": 2,
        "fillGradient": 0,
        "grid": {},
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 12,
            "y": 3
        },
        "hiddenSeries": false,
        "id": 20,
        "legend": {
            "alignAsTable": true,
            "avg": true,
            "current": false,
            "max": true,
            "min": true,
            "rightSide": true,
            "show": true,
            "sort": "avg",
            "sortDesc": true,
            "total": false,
            "values": true
        },
        "lines": true,
        "linewidth": 2,
        "links": [],
        "nullPointMode": "null as zero",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 1,
        "points": true,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "exemplar": true,
            "expr": "topk(10,\nrate(namedprocess_namegroup_cpu_seconds_total{groupname=~\"$processes\",instance=~\"$host\",mode=\"system\"}[$interval])\nor \n(irate(namedprocess_namegroup_cpu_seconds_total{groupname=~\"$processes\",instance=~\"$host\",mode=\"system\"}[5m])))",
            "format": "time_series",
            "interval": "$interval",
            "intervalFactor": 1,
            "legendFormat": "{{groupname}}",
            "metric": "process_namegroup_cpu_seconds_total",
            "refId": "A",
            "step": 10
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "Top processes by System CPU cores used",
        "tooltip": {
            "msResolution": false,
            "shared": true,
            "sort": 0,
            "value_type": "cumulative"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "percentunit",
            "logBase": 1,
            "min": 0,
            "show": true
            },
            {
            "decimals": 2,
            "format": "short",
            "logBase": 1,
            "show": false
            }
        ],
        "yaxis": {
            "align": false
        }
        },
        {
        "collapsed": false,
        "gridPos": {
            "h": 1,
            "w": 24,
            "x": 0,
            "y": 10
        },
        "id": 39,
        "panels": [],
        "title": "Process Memory Usage",
        "type": "row"
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "decimals": 2,
        "description": "Memory Used by Processes, counted as Resident Memory + Space used in Swap Space",
        "editable": true,
        "error": false,
        "fill": 2,
        "fillGradient": 0,
        "grid": {},
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 0,
            "y": 11
        },
        "hiddenSeries": false,
        "id": 22,
        "legend": {
            "alignAsTable": true,
            "avg": true,
            "current": false,
            "max": true,
            "min": true,
            "rightSide": true,
            "show": true,
            "sort": "avg",
            "sortDesc": true,
            "total": false,
            "values": true
        },
        "lines": true,
        "linewidth": 2,
        "links": [],
        "nullPointMode": "null as zero",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 1,
        "points": true,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "expr": "topk(5,(\r\n(avg_over_time(namedprocess_namegroup_memory_bytes{groupname=~\"$processes\", memtype=\"swapped\",instance=~\"$host\"}[$interval])+ ignoring (memtype) avg_over_time(namedprocess_namegroup_memory_bytes{groupname=~\"$processes\", memtype=\"resident\",instance=~\"$host\"}[$interval]))\r\nor\r\n(avg_over_time(namedprocess_namegroup_memory_bytes{groupname=~\"$processes\", memtype=\"swapped\",instance=~\"$host\"}[5m])+ ignoring (memtype) avg_over_time(namedprocess_namegroup_memory_bytes{groupname=~\"$processes\", memtype=\"resident\",instance=~\"$host\"}[5m]))\r\n))",
            "format": "time_series",
            "interval": "$interval",
            "intervalFactor": 1,
            "legendFormat": "{{groupname}}",
            "metric": "namedprocess_namegroup_memory_bytes",
            "refId": "A",
            "step": 10
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "Top processes by Used  memory",
        "tooltip": {
            "msResolution": false,
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "bytes",
            "logBase": 1,
            "min": 0,
            "show": true
            },
            {
            "format": "short",
            "logBase": 1,
            "show": true
            }
        ],
        "yaxis": {
            "align": false
        }
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "decimals": 2,
        "editable": true,
        "error": false,
        "fill": 2,
        "fillGradient": 0,
        "grid": {},
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 12,
            "y": 11
        },
        "hiddenSeries": false,
        "id": 5,
        "legend": {
            "alignAsTable": true,
            "avg": true,
            "current": false,
            "max": true,
            "min": true,
            "rightSide": true,
            "show": true,
            "sort": "avg",
            "sortDesc": true,
            "total": false,
            "values": true
        },
        "lines": true,
        "linewidth": 2,
        "links": [],
        "nullPointMode": "null as zero",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 1,
        "points": true,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "expr": "topk(5,\n(avg_over_time(namedprocess_namegroup_memory_bytes{groupname=~\"$processes\", memtype=\"resident\",instance=~\"$host\"}[$interval]) \nor\navg_over_time(namedprocess_namegroup_memory_bytes{groupname=~\"$processes\", memtype=\"resident\",instance=~\"$host\"}[5m])\n))",
            "format": "time_series",
            "interval": "$interval",
            "intervalFactor": 1,
            "legendFormat": "{{groupname}}",
            "metric": "namedprocess_namegroup_memory_bytes",
            "refId": "A",
            "step": 10
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "Top processes by Resident Memory",
        "tooltip": {
            "msResolution": false,
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "bytes",
            "logBase": 1,
            "min": 0,
            "show": true
            },
            {
            "format": "short",
            "logBase": 1,
            "show": true
            }
        ],
        "yaxis": {
            "align": false
        }
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "decimals": 2,
        "editable": true,
        "error": false,
        "fill": 2,
        "fillGradient": 0,
        "grid": {},
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 0,
            "y": 18
        },
        "hiddenSeries": false,
        "id": 6,
        "legend": {
            "alignAsTable": true,
            "avg": true,
            "current": false,
            "max": true,
            "min": true,
            "rightSide": true,
            "show": true,
            "sort": "avg",
            "sortDesc": true,
            "total": false,
            "values": true
        },
        "lines": true,
        "linewidth": 2,
        "links": [],
        "nullPointMode": "null as zero",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 1,
        "points": true,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "expr": "topk(5,(\navg_over_time(namedprocess_namegroup_memory_bytes{groupname=~\"$processes\", memtype=\"virtual\",instance=~\"$host\"}[$interval])\nor\navg_over_time(namedprocess_namegroup_memory_bytes{groupname=~\"$processes\", memtype=\"virtual\",instance=~\"$host\"}[5m])))\n",
            "format": "time_series",
            "interval": "$interval",
            "intervalFactor": 1,
            "legendFormat": "{{groupname}}",
            "metric": "namedprocess_namegroup_memory_bytes",
            "refId": "A",
            "step": 10
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "Top processes by Virtual memory",
        "tooltip": {
            "msResolution": false,
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "bytes",
            "logBase": 1,
            "min": 0,
            "show": true
            },
            {
            "format": "short",
            "logBase": 1,
            "show": true
            }
        ],
        "yaxis": {
            "align": false
        }
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "decimals": 2,
        "editable": true,
        "error": false,
        "fill": 2,
        "fillGradient": 0,
        "grid": {},
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 12,
            "y": 18
        },
        "hiddenSeries": false,
        "id": 21,
        "legend": {
            "alignAsTable": true,
            "avg": true,
            "current": false,
            "max": true,
            "min": true,
            "rightSide": true,
            "show": true,
            "sort": "avg",
            "sortDesc": true,
            "total": false,
            "values": true
        },
        "lines": true,
        "linewidth": 2,
        "links": [],
        "nullPointMode": "null as zero",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 1,
        "points": true,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "expr": "topk(5,(\navg_over_time(namedprocess_namegroup_memory_bytes{groupname=~\"$processes\", memtype=\"swapped\",instance=~\"$host\"}[$interval])\nor\navg_over_time(namedprocess_namegroup_memory_bytes{groupname=~\"$processes\", memtype=\"swapped\",instance=~\"$host\"}[5m])))\n",
            "format": "time_series",
            "hide": false,
            "interval": "$interval",
            "intervalFactor": 1,
            "legendFormat": "{{groupname}}",
            "metric": "namedprocess_namegroup_memory_bytes",
            "refId": "A",
            "step": 10
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "Top processes by Swapped Memory",
        "tooltip": {
            "msResolution": false,
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "bytes",
            "logBase": 1,
            "min": 0,
            "show": true
            },
            {
            "format": "short",
            "logBase": 1,
            "show": true
            }
        ],
        "yaxis": {
            "align": false
        }
        },
        {
        "collapsed": false,
        "gridPos": {
            "h": 1,
            "w": 24,
            "x": 0,
            "y": 25
        },
        "id": 37,
        "panels": [],
        "title": "Process Disk IO Usage",
        "type": "row"
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "decimals": 2,
        "editable": true,
        "error": false,
        "fill": 2,
        "fillGradient": 0,
        "grid": {},
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 0,
            "y": 26
        },
        "hiddenSeries": false,
        "id": 4,
        "legend": {
            "alignAsTable": true,
            "avg": true,
            "current": false,
            "max": true,
            "min": true,
            "rightSide": true,
            "show": true,
            "sort": "avg",
            "sortDesc": true,
            "total": false,
            "values": true
        },
        "lines": true,
        "linewidth": 2,
        "links": [],
        "nullPointMode": "null",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 1,
        "points": true,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "expr": "topk(5,(rate(namedprocess_namegroup_write_bytes_total{groupname=~\"$processes\",instance=~\"$host\"}[$interval]) or irate(namedprocess_namegroup_write_bytes_total{groupname=~\"$processes\",instance=~\"$host\"}[5m])))",
            "format": "time_series",
            "interval": "$interval",
            "intervalFactor": 1,
            "legendFormat": "{{groupname}}",
            "metric": "namedprocess_namegroup_read_bytes_total",
            "refId": "A",
            "step": 10
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "Top processes by Bytes Written",
        "tooltip": {
            "msResolution": false,
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "Bps",
            "logBase": 1,
            "min": 0,
            "show": true
            },
            {
            "format": "short",
            "logBase": 1,
            "show": true
            }
        ],
        "yaxis": {
            "align": false
        }
        },
        {
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisLabel": "",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "drawStyle": "line",
                "fillOpacity": 20,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "lineInterpolation": "linear",
                "lineWidth": 2,
                "pointSize": 4,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "always",
                "spanNulls": true,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "min": 0,
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            },
            "unit": "Bps"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 12,
            "y": 26
        },
        "id": 3,
        "links": [],
        "options": {
            "legend": {
            "calcs": [
                "mean",
                "max",
                "min"
            ],
            "displayMode": "table",
            "placement": "right"
            },
            "tooltip": {
            "mode": "multi",
            "sort": "none"
            }
        },
        "pluginVersion": "8.4.1",
        "targets": [
            {
            "exemplar": true,
            "expr": "topk(10,(rate(namedprocess_namegroup_read_bytes_total{groupname=~\"$processes\",instance=~\"$host\"}[$interval]) or irate(namedprocess_namegroup_read_bytes_total{groupname=~\"$processes\",instance=~\"$host\"}[5m])))",
            "format": "time_series",
            "interval": "$interval",
            "intervalFactor": 1,
            "legendFormat": "{{groupname}}",
            "metric": "namedprocess_namegroup_read_bytes_total",
            "refId": "A",
            "step": 10
            }
        ],
        "title": "Top processes by Bytes Read",
        "type": "timeseries"
        },
        {
        "collapsed": false,
        "gridPos": {
            "h": 1,
            "w": 24,
            "x": 0,
            "y": 33
        },
        "id": 33,
        "panels": [],
        "title": "Process and Thread Counts",
        "type": "row"
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "decimals": 2,
        "editable": true,
        "error": false,
        "fill": 2,
        "fillGradient": 0,
        "grid": {},
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 0,
            "y": 34
        },
        "hiddenSeries": false,
        "id": 1,
        "legend": {
            "alignAsTable": true,
            "avg": true,
            "current": false,
            "hideZero": true,
            "max": true,
            "min": true,
            "rightSide": true,
            "show": true,
            "sort": "avg",
            "sortDesc": true,
            "total": false,
            "values": true
        },
        "lines": false,
        "linewidth": 2,
        "links": [],
        "nullPointMode": "null as zero",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 1,
        "points": true,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "expr": "topk(5,(max_over_time(namedprocess_namegroup_num_procs{groupname=~\"$processes\",instance=~\"$host\"}[$interval]) \nor max_over_time(namedprocess_namegroup_num_procs{groupname=~\"$processes\",instance=~\"$host\"}[5m])))",
            "format": "time_series",
            "hide": false,
            "interval": "$interval",
            "intervalFactor": 1,
            "legendFormat": "{{groupname}}",
            "metric": "process_namegroup_num_procs",
            "refId": "A",
            "step": 10
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "Top processes by number of  processes instances",
        "tooltip": {
            "msResolution": false,
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "short",
            "logBase": 1,
            "min": "0",
            "show": true
            },
            {
            "format": "short",
            "logBase": 1,
            "show": true
            }
        ],
        "yaxis": {
            "align": false
        }
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "decimals": 2,
        "fill": 2,
        "fillGradient": 0,
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 12,
            "y": 34
        },
        "hiddenSeries": false,
        "id": 10,
        "legend": {
            "alignAsTable": true,
            "avg": true,
            "current": false,
            "hideZero": true,
            "max": true,
            "min": true,
            "rightSide": true,
            "show": true,
            "sort": "avg",
            "sortDesc": true,
            "total": false,
            "values": true
        },
        "lines": false,
        "linewidth": 2,
        "links": [],
        "nullPointMode": "null as zero",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 1,
        "points": true,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "expr": "topk(5,(max_over_time(namedprocess_namegroup_num_threads{groupname=~\"$processes\",instance=~\"$host\"}[$interval]) or\nmax_over_time(namedprocess_namegroup_num_threads{groupname=~\"$processes\",instance=~\"$host\"}[5m])))",
            "format": "time_series",
            "interval": "$interval",
            "intervalFactor": 1,
            "legendFormat": "{{groupname}}",
            "refId": "A"
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "Top processes by number of threads",
        "tooltip": {
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "short",
            "logBase": 1,
            "show": true
            },
            {
            "format": "short",
            "logBase": 1,
            "show": false
            }
        ],
        "yaxis": {
            "align": false
        }
        },
        {
        "collapsed": true,
        "gridPos": {
            "h": 1,
            "w": 24,
            "x": 0,
            "y": 41
        },
        "id": 43,
        "panels": [
            {
            "aliasColors": {},
            "bars": false,
            "dashLength": 10,
            "dashes": false,
            "decimals": 2,
            "fill": 2,
            "gridPos": {
                "h": 7,
                "w": 12,
                "x": 0,
                "y": 7
            },
            "id": 24,
            "legend": {
                "alignAsTable": true,
                "avg": true,
                "current": false,
                "max": true,
                "min": true,
                "rightSide": true,
                "show": true,
                "sort": "avg",
                "sortDesc": true,
                "total": false,
                "values": true
            },
            "lines": false,
            "linewidth": 2,
            "links": [],
            "nullPointMode": "null as zero",
            "percentage": false,
            "pointradius": 1,
            "points": true,
            "renderer": "flot",
            "seriesOverrides": [],
            "spaceLength": 10,
            "stack": false,
            "steppedLine": false,
            "targets": [
                {
                "expr": "topk(5,(\nrate(namedprocess_namegroup_context_switches_total{groupname=~\"$processes\",instance=~\"$host\",ctxswitchtype=\"voluntary\"}[$interval]) or\nirate(namedprocess_namegroup_context_switches_total{groupname=~\"$processes\",instance=~\"$host\",ctxswitchtype=\"voluntary\"}[5m])))",
                "format": "time_series",
                "interval": "$interval",
                "intervalFactor": 1,
                "legendFormat": "{{groupname}}",
                "refId": "A"
                }
            ],
            "thresholds": [],
            "title": "Top Processes by Voluntary Context Switches",
            "tooltip": {
                "shared": true,
                "sort": 0,
                "value_type": "individual"
            },
            "type": "graph",
            "xaxis": {
                "mode": "time",
                "show": true,
                "values": []
            },
            "yaxes": [
                {
                "format": "ops",
                "logBase": 1,
                "min": "0",
                "show": true
                },
                {
                "format": "short",
                "logBase": 1,
                "show": false
                }
            ],
            "yaxis": {
                "align": false
            }
            },
            {
            "aliasColors": {},
            "bars": false,
            "dashLength": 10,
            "dashes": false,
            "decimals": 2,
            "fill": 2,
            "gridPos": {
                "h": 7,
                "w": 12,
                "x": 12,
                "y": 7
            },
            "id": 25,
            "legend": {
                "alignAsTable": true,
                "avg": true,
                "current": false,
                "max": true,
                "min": true,
                "rightSide": true,
                "show": true,
                "sort": "avg",
                "sortDesc": true,
                "total": false,
                "values": true
            },
            "lines": false,
            "linewidth": 2,
            "links": [],
            "nullPointMode": "null as zero",
            "percentage": false,
            "pointradius": 1,
            "points": true,
            "renderer": "flot",
            "seriesOverrides": [],
            "spaceLength": 10,
            "stack": false,
            "steppedLine": false,
            "targets": [
                {
                "expr": "topk(5,(\nrate(namedprocess_namegroup_context_switches_total{groupname=~\"$processes\",instance=~\"$host\",ctxswitchtype=\"nonvoluntary\"}[$interval]) or\nirate(namedprocess_namegroup_context_switches_total{groupname=~\"$processes\",instance=~\"$host\",ctxswitchtype=\"nonvoluntary\"}[5m])))",
                "format": "time_series",
                "interval": "$interval",
                "intervalFactor": 1,
                "legendFormat": "{{groupname}}",
                "refId": "A"
                }
            ],
            "thresholds": [],
            "title": "Top Processes by  Non-Voluntary Context Switches",
            "tooltip": {
                "shared": true,
                "sort": 0,
                "value_type": "individual"
            },
            "type": "graph",
            "xaxis": {
                "mode": "time",
                "show": true,
                "values": []
            },
            "yaxes": [
                {
                "format": "ops",
                "logBase": 1,
                "min": "0",
                "show": true
                },
                {
                "format": "short",
                "logBase": 1,
                "show": false
                }
            ],
            "yaxis": {
                "align": false
            }
            }
        ],
        "title": "Process Context Switches",
        "type": "row"
        },
        {
        "collapsed": true,
        "gridPos": {
            "h": 1,
            "w": 24,
            "x": 0,
            "y": 42
        },
        "id": 35,
        "panels": [
            {
            "aliasColors": {},
            "bars": false,
            "dashLength": 10,
            "dashes": false,
            "fill": 2,
            "gridPos": {
                "h": 7,
                "w": 12,
                "x": 0,
                "y": 8
            },
            "id": 13,
            "legend": {
                "alignAsTable": true,
                "avg": true,
                "current": false,
                "max": true,
                "min": true,
                "rightSide": true,
                "show": true,
                "sort": "avg",
                "sortDesc": true,
                "total": false,
                "values": true
            },
            "lines": false,
            "linewidth": 2,
            "links": [],
            "nullPointMode": "null as zero",
            "percentage": false,
            "pointradius": 1,
            "points": true,
            "renderer": "flot",
            "seriesOverrides": [],
            "spaceLength": 10,
            "stack": false,
            "steppedLine": true,
            "targets": [
                {
                "expr": "topk(5,(max_over_time(namedprocess_namegroup_open_filedesc{groupname=~\"$processes\",instance=~\"$host\"}[$interval]) or\nmax_over_time(namedprocess_namegroup_open_filedesc{groupname=~\"$processes\",instance=~\"$host\"}[5m])))",
                "format": "time_series",
                "hide": false,
                "interval": "$interval",
                "intervalFactor": 1,
                "legendFormat": "{{groupname}}",
                "refId": "A"
                }
            ],
            "thresholds": [],
            "title": "Top processes by Open File Descriptors",
            "tooltip": {
                "shared": true,
                "sort": 0,
                "value_type": "individual"
            },
            "type": "graph",
            "xaxis": {
                "mode": "time",
                "show": true,
                "values": []
            },
            "yaxes": [
                {
                "format": "short",
                "logBase": 1,
                "min": "0",
                "show": true
                },
                {
                "format": "short",
                "logBase": 1,
                "show": false
                }
            ],
            "yaxis": {
                "align": false
            }
            },
            {
            "aliasColors": {},
            "bars": false,
            "dashLength": 10,
            "dashes": false,
            "fill": 2,
            "gridPos": {
                "h": 7,
                "w": 12,
                "x": 12,
                "y": 8
            },
            "id": 7,
            "legend": {
                "alignAsTable": true,
                "avg": true,
                "current": false,
                "max": true,
                "min": true,
                "rightSide": true,
                "show": true,
                "sort": "avg",
                "sortDesc": true,
                "total": false,
                "values": true
            },
            "lines": false,
            "linewidth": 2,
            "links": [],
            "nullPointMode": "null as zero",
            "percentage": false,
            "pointradius": 1,
            "points": true,
            "renderer": "flot",
            "seriesOverrides": [],
            "spaceLength": 10,
            "stack": false,
            "steppedLine": true,
            "targets": [
                {
                "expr": "topk(5,(\nmax_over_time(namedprocess_namegroup_worst_fd_ratio{groupname=~\"$processes\",instance=~\"$host\"}[$interval]) or\nmax_over_time(namedprocess_namegroup_worst_fd_ratio{groupname=~\"$processes\",instance=~\"$host\"}[5m])\n))*100",
                "format": "time_series",
                "interval": "$interval",
                "intervalFactor": 1,
                "legendFormat": "{{groupname}}",
                "refId": "A"
                }
            ],
            "thresholds": [],
            "title": "Top processes by File Descriptor Usage Percent",
            "tooltip": {
                "shared": true,
                "sort": 0,
                "value_type": "individual"
            },
            "type": "graph",
            "xaxis": {
                "mode": "time",
                "show": true,
                "values": []
            },
            "yaxes": [
                {
                "format": "percent",
                "label": "",
                "logBase": 1,
                "min": "0",
                "show": true
                },
                {
                "format": "short",
                "logBase": 1,
                "show": false
                }
            ],
            "yaxis": {
                "align": false
            }
            }
        ],
        "title": "Process File Descriptors",
        "type": "row"
        },
        {
        "collapsed": true,
        "gridPos": {
            "h": 1,
            "w": 24,
            "x": 0,
            "y": 43
        },
        "id": 27,
        "panels": [
            {
            "aliasColors": {},
            "bars": false,
            "dashLength": 10,
            "dashes": false,
            "decimals": 2,
            "fill": 2,
            "fillGradient": 0,
            "gridPos": {
                "h": 7,
                "w": 12,
                "x": 0,
                "y": 37
            },
            "hiddenSeries": false,
            "id": 8,
            "legend": {
                "alignAsTable": true,
                "avg": true,
                "current": false,
                "max": true,
                "min": true,
                "rightSide": true,
                "show": true,
                "sort": "avg",
                "sortDesc": true,
                "total": false,
                "values": true
            },
            "lines": false,
            "linewidth": 2,
            "links": [],
            "nullPointMode": "null as zero",
            "options": {
                "alertThreshold": true
            },
            "percentage": false,
            "pluginVersion": "8.4.1",
            "pointradius": 1,
            "points": true,
            "renderer": "flot",
            "seriesOverrides": [],
            "spaceLength": 10,
            "stack": false,
            "steppedLine": false,
            "targets": [
                {
                "expr": "topk(5,(\nrate(namedprocess_namegroup_major_page_faults_total{groupname=~\"$processes\",instance=~\"$host\"}[$interval]) or\nirate(namedprocess_namegroup_major_page_faults_total{groupname=~\"$processes\",instance=~\"$host\"}[5m])))",
                "format": "time_series",
                "interval": "$interval",
                "intervalFactor": 1,
                "legendFormat": "{{groupname}}",
                "refId": "A"
                }
            ],
            "thresholds": [],
            "timeRegions": [],
            "title": "Top processes by Major Page Faults",
            "tooltip": {
                "shared": true,
                "sort": 0,
                "value_type": "individual"
            },
            "type": "graph",
            "xaxis": {
                "mode": "time",
                "show": true,
                "values": []
            },
            "yaxes": [
                {
                "format": "ops",
                "logBase": 1,
                "min": "0",
                "show": true
                },
                {
                "format": "short",
                "logBase": 1,
                "show": false
                }
            ],
            "yaxis": {
                "align": false
            }
            },
            {
            "aliasColors": {},
            "bars": false,
            "dashLength": 10,
            "dashes": false,
            "decimals": 2,
            "fill": 2,
            "fillGradient": 0,
            "gridPos": {
                "h": 7,
                "w": 12,
                "x": 12,
                "y": 37
            },
            "hiddenSeries": false,
            "id": 9,
            "legend": {
                "alignAsTable": true,
                "avg": true,
                "current": false,
                "max": true,
                "min": true,
                "rightSide": true,
                "show": true,
                "sort": "avg",
                "sortDesc": true,
                "total": false,
                "values": true
            },
            "lines": false,
            "linewidth": 2,
            "links": [],
            "nullPointMode": "null as zero",
            "options": {
                "alertThreshold": true
            },
            "percentage": false,
            "pluginVersion": "8.4.1",
            "pointradius": 1,
            "points": true,
            "renderer": "flot",
            "seriesOverrides": [],
            "spaceLength": 10,
            "stack": false,
            "steppedLine": false,
            "targets": [
                {
                "expr": "topk(5,(\nrate(namedprocess_namegroup_minor_page_faults_total{groupname=~\"$processes\",instance=~\"$host\"}[$interval]) or\nirate(namedprocess_namegroup_minor_page_faults_total{groupname=~\"$processes\",instance=~\"$host\"}[5m])))",
                "format": "time_series",
                "interval": "$interval",
                "intervalFactor": 1,
                "legendFormat": "{{groupname}}",
                "refId": "A"
                }
            ],
            "thresholds": [],
            "timeRegions": [],
            "title": "Top processes by Minor Page Faults",
            "tooltip": {
                "shared": true,
                "sort": 0,
                "value_type": "individual"
            },
            "type": "graph",
            "xaxis": {
                "mode": "time",
                "show": true,
                "values": []
            },
            "yaxes": [
                {
                "format": "ops",
                "logBase": 1,
                "min": "0",
                "show": true
                },
                {
                "format": "short",
                "logBase": 1,
                "show": false
                }
            ],
            "yaxis": {
                "align": false
            }
            }
        ],
        "title": "Process Page Faults",
        "type": "row"
        },
        {
        "collapsed": false,
        "gridPos": {
            "h": 1,
            "w": 24,
            "x": 0,
            "y": 44
        },
        "id": 29,
        "panels": [],
        "title": "Process Statuses",
        "type": "row"
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "decimals": 2,
        "description": "",
        "fill": 2,
        "fillGradient": 0,
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 0,
            "y": 45
        },
        "hiddenSeries": false,
        "id": 11,
        "legend": {
            "alignAsTable": true,
            "avg": true,
            "current": false,
            "hideZero": false,
            "max": true,
            "min": true,
            "rightSide": true,
            "show": true,
            "sort": "avg",
            "sortDesc": true,
            "total": false,
            "values": true
        },
        "lines": false,
        "linewidth": 2,
        "links": [],
        "nullPointMode": "null as zero",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 1,
        "points": true,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": true,
        "targets": [
            {
            "expr": "topk(5,(\nmax_over_time(namedprocess_namegroup_states{instance=~\"$host\", groupname=~\"$processes\", state=\"Running\"}[$interval]) or\nmax_over_time(namedprocess_namegroup_states{instance=~\"$host\", groupname=~\"$processes\", state=\"Running\"}[5m])))",
            "format": "time_series",
            "interval": "$interval",
            "intervalFactor": 1,
            "legendFormat": "{{groupname}}",
            "refId": "A"
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "Top running processes",
        "tooltip": {
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "short",
            "logBase": 1,
            "min": "0",
            "show": true
            },
            {
            "format": "short",
            "logBase": 1,
            "show": false
            }
        ],
        "yaxis": {
            "align": false
        }
        },
        {
        "aliasColors": {},
        "bars": false,
        "dashLength": 10,
        "dashes": false,
        "decimals": 2,
        "description": "",
        "fill": 1,
        "fillGradient": 0,
        "gridPos": {
            "h": 7,
            "w": 12,
            "x": 12,
            "y": 45
        },
        "hiddenSeries": false,
        "id": 14,
        "legend": {
            "alignAsTable": true,
            "avg": true,
            "current": false,
            "max": true,
            "min": true,
            "rightSide": true,
            "show": true,
            "sort": "avg",
            "sortDesc": true,
            "total": false,
            "values": true
        },
        "lines": false,
        "linewidth": 1,
        "links": [],
        "nullPointMode": "null as zero",
        "options": {
            "alertThreshold": true
        },
        "percentage": false,
        "pluginVersion": "8.4.1",
        "pointradius": 1,
        "points": true,
        "renderer": "flot",
        "seriesOverrides": [],
        "spaceLength": 10,
        "stack": false,
        "steppedLine": false,
        "targets": [
            {
            "expr": "topk(5,(\nmax_over_time(namedprocess_namegroup_states{instance=~\"$host\", groupname=~\"$processes\", state=\"Waiting\"}[$interval]) or\nmax_over_time(namedprocess_namegroup_states{instance=~\"$host\", groupname=~\"$processes\", state=\"Waiting\"}[5m])))",
            "format": "time_series",
            "interval": "$interval",
            "intervalFactor": 1,
            "legendFormat": "{{groupname}}",
            "refId": "A"
            }
        ],
        "thresholds": [],
        "timeRegions": [],
        "title": "Top of processes waiting on IO",
        "tooltip": {
            "shared": true,
            "sort": 0,
            "value_type": "individual"
        },
        "type": "graph",
        "xaxis": {
            "mode": "time",
            "show": true,
            "values": []
        },
        "yaxes": [
            {
            "format": "short",
            "logBase": 1,
            "min": "0",
            "show": true
            },
            {
            "format": "short",
            "logBase": 1,
            "show": false
            }
        ],
        "yaxis": {
            "align": false
        }
        },
        {
        "collapsed": true,
        "gridPos": {
            "h": 1,
            "w": 24,
            "x": 0,
            "y": 52
        },
        "id": 45,
        "panels": [
            {
            "aliasColors": {},
            "bars": false,
            "dashLength": 10,
            "dashes": false,
            "decimals": 2,
            "description": "",
            "fill": 1,
            "fillGradient": 0,
            "gridPos": {
                "h": 7,
                "w": 12,
                "x": 0,
                "y": 46
            },
            "hiddenSeries": false,
            "id": 46,
            "legend": {
                "alignAsTable": true,
                "avg": true,
                "current": false,
                "max": true,
                "min": true,
                "rightSide": true,
                "show": true,
                "sort": "avg",
                "sortDesc": true,
                "total": false,
                "values": true
            },
            "lines": false,
            "linewidth": 1,
            "links": [],
            "nullPointMode": "null as zero",
            "options": {
                "alertThreshold": true
            },
            "percentage": false,
            "pluginVersion": "8.4.1",
            "pointradius": 1,
            "points": true,
            "renderer": "flot",
            "seriesOverrides": [],
            "spaceLength": 10,
            "stack": false,
            "steppedLine": false,
            "targets": [
                {
                "expr": "topk(5,sum(avg_over_time(namedprocess_namegroup_threads_wchan{instance=~\"$host\", groupname=~\"$processes\"}[$interval])) by (wchan) )",
                "format": "time_series",
                "interval": "$interval",
                "intervalFactor": 1,
                "legendFormat": "{{wchan}}",
                "refId": "A"
                }
            ],
            "thresholds": [],
            "timeRegions": [],
            "title": "Kernel waits for $processes",
            "tooltip": {
                "shared": true,
                "sort": 0,
                "value_type": "individual"
            },
            "type": "graph",
            "xaxis": {
                "mode": "time",
                "show": true,
                "values": []
            },
            "yaxes": [
                {
                "format": "short",
                "logBase": 1,
                "min": "0",
                "show": true
                },
                {
                "format": "short",
                "logBase": 1,
                "show": false
                }
            ],
            "yaxis": {
                "align": false
            }
            },
            {
            "aliasColors": {},
            "bars": false,
            "dashLength": 10,
            "dashes": false,
            "decimals": 2,
            "description": "",
            "fill": 1,
            "fillGradient": 0,
            "gridPos": {
                "h": 7,
                "w": 12,
                "x": 12,
                "y": 46
            },
            "hiddenSeries": false,
            "id": 47,
            "legend": {
                "alignAsTable": true,
                "avg": true,
                "current": false,
                "max": true,
                "min": true,
                "rightSide": true,
                "show": true,
                "sort": "avg",
                "sortDesc": true,
                "total": false,
                "values": true
            },
            "lines": false,
            "linewidth": 1,
            "links": [],
            "nullPointMode": "null as zero",
            "options": {
                "alertThreshold": true
            },
            "percentage": false,
            "pluginVersion": "8.4.1",
            "pointradius": 1,
            "points": true,
            "renderer": "flot",
            "seriesOverrides": [],
            "spaceLength": 10,
            "stack": false,
            "steppedLine": false,
            "targets": [
                {
                "expr": "topk(5,sum(avg_over_time(namedprocess_namegroup_threads_wchan{instance=~\"$host\", groupname=~\"$processes\"}[$interval])) by (wchan,groupname) )",
                "format": "time_series",
                "interval": "$interval",
                "intervalFactor": 1,
                "legendFormat": "{{groupname}} : {{wchan}}",
                "refId": "A"
                }
            ],
            "thresholds": [],
            "timeRegions": [],
            "title": "Kernel wait Details for $processes",
            "tooltip": {
                "shared": true,
                "sort": 0,
                "value_type": "individual"
            },
            "type": "graph",
            "xaxis": {
                "mode": "time",
                "show": true,
                "values": []
            },
            "yaxes": [
                {
                "format": "short",
                "logBase": 1,
                "min": "0",
                "show": true
                },
                {
                "format": "short",
                "logBase": 1,
                "show": false
                }
            ],
            "yaxis": {
                "align": false
            }
            }
        ],
        "title": "Process Kernel Waits (WCHAN)",
        "type": "row"
        },
        {
        "collapsed": true,
        "gridPos": {
            "h": 1,
            "w": 24,
            "x": 0,
            "y": 53
        },
        "id": 41,
        "panels": [
            {
            "columns": [],
            "fontSize": "100%",
            "gridPos": {
                "h": 10,
                "w": 24,
                "x": 0,
                "y": 12
            },
            "id": 19,
            "links": [],
            "scroll": true,
            "showHeader": true,
            "sort": {
                "col": 4,
                "desc": true
            },
            "styles": [
                {
                "alias": "Time",
                "align": "auto",
                "dateFormat": "YYYY-MM-DD HH:mm:ss",
                "pattern": "Time",
                "type": "hidden"
                },
                {
                "alias": "Uptime",
                "align": "auto",
                "colors": [
                    "rgba(245, 54, 54, 0.9)",
                    "rgba(237, 129, 40, 0.89)",
                    "rgba(50, 172, 45, 0.97)"
                ],
                "dateFormat": "YYYY-MM-DD HH:mm:ss",
                "decimals": 2,
                "pattern": "Value",
                "thresholds": [],
                "type": "number",
                "unit": "s"
                },
                {
                "alias": "",
                "align": "auto",
                "colors": [
                    "rgba(245, 54, 54, 0.9)",
                    "rgba(237, 129, 40, 0.89)",
                    "rgba(50, 172, 45, 0.97)"
                ],
                "dateFormat": "YYYY-MM-DD HH:mm:ss",
                "decimals": 2,
                "pattern": "instance",
                "sanitize": false,
                "thresholds": [],
                "type": "hidden",
                "unit": "short"
                },
                {
                "alias": "",
                "align": "auto",
                "colors": [
                    "rgba(245, 54, 54, 0.9)",
                    "rgba(237, 129, 40, 0.89)",
                    "rgba(50, 172, 45, 0.97)"
                ],
                "dateFormat": "YYYY-MM-DD HH:mm:ss",
                "decimals": 2,
                "pattern": "job",
                "thresholds": [],
                "type": "hidden",
                "unit": "short"
                },
                {
                "alias": "Processes",
                "align": "auto",
                "colors": [
                    "rgba(245, 54, 54, 0.9)",
                    "rgba(237, 129, 40, 0.89)",
                    "rgba(50, 172, 45, 0.97)"
                ],
                "dateFormat": "YYYY-MM-DD HH:mm:ss",
                "decimals": 2,
                "pattern": "groupname",
                "thresholds": [],
                "type": "string",
                "unit": "short"
                },
                {
                "alias": "",
                "align": "auto",
                "colors": [
                    "rgba(245, 54, 54, 0.9)",
                    "rgba(237, 129, 40, 0.89)",
                    "rgba(50, 172, 45, 0.97)"
                ],
                "decimals": 2,
                "pattern": "/.*/",
                "thresholds": [],
                "type": "number",
                "unit": "short"
                }
            ],
            "targets": [
                {
                "expr": "time()-(namedprocess_namegroup_oldest_start_time_seconds{instance=~\"$host\"}>0)",
                "format": "table",
                "instant": true,
                "interval": "",
                "intervalFactor": 1,
                "legendFormat": "",
                "refId": "A"
                }
            ],
            "title": "Processes by uptime",
            "transform": "table",
            "type": "table-old"
            }
        ],
        "title": "Process Uptime",
        "type": "row"
        }
    ],
    "refresh": "1m",
    "schemaVersion": 35,
    "style": "dark",
    "tags": [
        "Insight"
    ],
    "templating": {
        "list": [
        {
            "auto": true,
            "auto_count": 200,
            "auto_min": "1s",
            "current": {
            "selected": false,
            "text": "auto",
            "value": "$__auto_interval_interval"
            },
            "hide": 0,
            "includeAll": false,
            "label": "Interval",
            "multi": false,
            "name": "interval",
            "options": [
            {
                "selected": true,
                "text": "auto",
                "value": "$__auto_interval_interval"
            },
            {
                "selected": false,
                "text": "1s",
                "value": "1s"
            },
            {
                "selected": false,
                "text": "5s",
                "value": "5s"
            },
            {
                "selected": false,
                "text": "1m",
                "value": "1m"
            },
            {
                "selected": false,
                "text": "5m",
                "value": "5m"
            },
            {
                "selected": false,
                "text": "1h",
                "value": "1h"
            },
            {
                "selected": false,
                "text": "6h",
                "value": "6h"
            },
            {
                "selected": false,
                "text": "1d",
                "value": "1d"
            }
            ],
            "query": "1s,5s,1m,5m,1h,6h,1d",
            "queryValue": "",
            "refresh": 2,
            "skipUrlSync": false,
            "type": "interval"
        },
        {
            "current": {
            "isNone": true,
            "selected": false,
            "text": "None",
            "value": ""
            },
            "definition": "",
            "hide": 0,
            "includeAll": false,
            "label": "Host",
            "multi": false,
            "name": "host",
            "options": [],
            "query": {
            "query": "label_values(namedprocess_namegroup_num_procs, instance)",
            "refId": "Prometheus-host-Variable-Query"
            },
            "refresh": 2,
            "regex": "",
            "skipUrlSync": false,
            "sort": 1,
            "tagValuesQuery": "",
            "tagsQuery": "",
            "type": "query",
            "useTags": false
        },
        {
            "allValue": ".+",
            "current": {
            "selected": false,
            "text": "All",
            "value": "$__all"
            },

            "definition": "",
            "hide": 0,
            "includeAll": true,
            "label": "Processes",
            "multi": true,
            "name": "processes",
            "options": [],
            "query": {
            "query": "label_values(namedprocess_namegroup_cpu_user_seconds_total{instance=~\"$host\"},groupname)",
            "refId": "Prometheus-processes-Variable-Query"
            },
            "refresh": 2,
            "regex": "",
            "skipUrlSync": false,
            "sort": 0,
            "tagValuesQuery": "",
            "tagsQuery": "",
            "type": "query",
            "useTags": false
        }
        ]
    },
    "time": {
        "from": "now-1h",
        "to": "now"
    },
    "timepicker": {
        "refresh_intervals": [
        "5s",
        "10s",
        "30s",
        "1m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "1d"
        ],
        "time_options": [
        "5m",
        "15m",
        "1h",
        "6h",
        "12h",
        "24h",
        "2d",
        "7d",
        "30d"
        ]
    },
    "timezone": "browser",
    "title": "System Processes Metrics",
    "uid": "oZpynZ7mz",
    "version": 4,
    "weekStart": ""
    }
"""
prometheus_config_template=r"""
# my global config
global:
  scrape_interval: 15s # Set the scrape interval to every 15 seconds. Default is every 1 minute.
  evaluation_interval: 15s # Evaluate rules every 15 seconds. The default is every 1 minute.
  # scrape_timeout is set to the global default (10s).
  external_labels:
    prometheus_env: test

# Alertmanager configuration
#alerting:
#  alertmanagers:
#    - static_configs:
#        - targets:
#           - 10.30.100.244:9093

# Load rules once and periodically evaluate them according to the global 'evaluation_interval'.
rule_files:
  - "/etc/prometheus/rules/*_rule.yaml"

# A scrape configuration containing exactly one endpoint to scrape:
# Here it's Prometheus itself.
scrape_configs:
  # The job name is added as a label `job=<job_name>` to any timeseries scraped from this config.
  - job_name: "prometheus"
    static_configs:
    - targets: ["localhost:9090"]
"""
vllm_dashboard=r"""
    {
    "annotations": {
        "list": [
        {
            "builtIn": 1,
            "datasource": {
            "type": "grafana",
            "uid": "-- Grafana --"
            },
            "enable": true,
            "hide": true,
            "iconColor": "rgba(0, 211, 255, 1)",
            "name": "Annotations & Alerts",
            "target": {
            "limit": 100,
            "matchAny": false,
            "tags": [],
            "type": "dashboard"
            },
            "type": "dashboard"
        }
        ]
    },
    "description": "Monitoring vLLM Inference Server",
    "editable": true,
    "fiscalYearStartMonth": 0,
    "graphTooltip": 0,
    "id": 1,
    "links": [],
    "liveNow": false,
    "panels": [
        {
        "description": "End to end request latency measured in seconds.",
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisCenteredZero": false,
                "axisColorMode": "text",
                "axisLabel": "",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "drawStyle": "line",
                "fillOpacity": 0,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "lineInterpolation": "linear",
                "lineWidth": 1,
                "pointSize": 5,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "auto",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            },
            "unit": "s"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 0,
            "y": 0
        },
        "id": 9,
        "options": {
            "legend": {
            "calcs": [],
            "displayMode": "list",
            "placement": "bottom",
            "showLegend": true
            },
            "tooltip": {
            "mode": "single",
            "sort": "none"
            }
        },
        "targets": [
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "histogram_quantile(0.99, sum by(le) (rate(vllm:e2e_request_latency_seconds_bucket{model_name=\"$model_name\"}[$__rate_interval])))",
            "fullMetaSearch": false,
            "includeNullMetadata": false,
            "instant": false,
            "legendFormat": "P99",
            "range": true,
            "refId": "A",
            "useBackend": false
            },
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "histogram_quantile(0.95, sum by(le) (rate(vllm:e2e_request_latency_seconds_bucket{model_name=\"$model_name\"}[$__rate_interval])))",
            "fullMetaSearch": false,
            "hide": false,
            "includeNullMetadata": false,
            "instant": false,
            "legendFormat": "P95",
            "range": true,
            "refId": "B",
            "useBackend": false
            },
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "histogram_quantile(0.9, sum by(le) (rate(vllm:e2e_request_latency_seconds_bucket{model_name=\"$model_name\"}[$__rate_interval])))",
            "fullMetaSearch": false,
            "hide": false,
            "includeNullMetadata": false,
            "instant": false,
            "legendFormat": "P90",
            "range": true,
            "refId": "C",
            "useBackend": false
            },
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "histogram_quantile(0.5, sum by(le) (rate(vllm:e2e_request_latency_seconds_bucket{model_name=\"$model_name\"}[$__rate_interval])))",
            "fullMetaSearch": false,
            "hide": false,
            "includeNullMetadata": false,
            "instant": false,
            "legendFormat": "P50",
            "range": true,
            "refId": "D",
            "useBackend": false
            },
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "editorMode": "code",
            "expr": "rate(vllm:e2e_request_latency_seconds_sum{model_name=\"$model_name\"}[$__rate_interval])\n/\nrate(vllm:e2e_request_latency_seconds_count{model_name=\"$model_name\"}[$__rate_interval])",
            "hide": false,
            "instant": false,
            "legendFormat": "Average",
            "range": true,
            "refId": "E"
            }
        ],
        "title": "E2E Request Latency",
        "type": "timeseries"
        },
        {
        "datasource": {
            "type": "prometheus",
            "uid": "${DS_PROMETHEUS}"
        },
        "description": "Number of tokens processed per second",
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisCenteredZero": false,
                "axisColorMode": "text",
                "axisLabel": "",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "drawStyle": "line",
                "fillOpacity": 0,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "lineInterpolation": "linear",
                "lineWidth": 1,
                "pointSize": 5,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "auto",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            }
            },
            "overrides": []
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 12,
            "y": 0
        },
        "id": 8,
        "options": {
            "legend": {
            "calcs": [],
            "displayMode": "list",
            "placement": "bottom",
            "showLegend": true
            },
            "tooltip": {
            "mode": "single",
            "sort": "none"
            }
        },
        "targets": [
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "rate(vllm:prompt_tokens_total{model_name=\"$model_name\"}[$__rate_interval])",
            "fullMetaSearch": false,
            "includeNullMetadata": false,
            "instant": false,
            "legendFormat": "Prompt Tokens/Sec",
            "range": true,
            "refId": "A",
            "useBackend": false
            },
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "rate(vllm:generation_tokens_total{model_name=\"$model_name\"}[$__rate_interval])",
            "fullMetaSearch": false,
            "hide": false,
            "includeNullMetadata": false,
            "instant": false,
            "legendFormat": "Generation Tokens/Sec",
            "range": true,
            "refId": "B",
            "useBackend": false
            }
        ],
        "title": "Token Throughput",
        "type": "timeseries"
        },
        {
        "datasource": {
            "type": "prometheus",
            "uid": "${DS_PROMETHEUS}"
        },
        "description": "Inter token latency in seconds.",
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisCenteredZero": false,
                "axisColorMode": "text",
                "axisLabel": "",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "drawStyle": "line",
                "fillOpacity": 0,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "lineInterpolation": "linear",
                "lineWidth": 1,
                "pointSize": 5,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "auto",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            },
            "unit": "s"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 0,
            "y": 8
        },
        "id": 10,
        "options": {
            "legend": {
            "calcs": [],
            "displayMode": "list",
            "placement": "bottom",
            "showLegend": true
            },
            "tooltip": {
            "mode": "single",
            "sort": "none"
            }
        },
        "targets": [
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "histogram_quantile(0.99, sum by(le) (rate(vllm:time_per_output_token_seconds_bucket{model_name=\"$model_name\"}[$__rate_interval])))",
            "fullMetaSearch": false,
            "includeNullMetadata": false,
            "instant": false,
            "legendFormat": "P99",
            "range": true,
            "refId": "A",
            "useBackend": false
            },
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "histogram_quantile(0.95, sum by(le) (rate(vllm:time_per_output_token_seconds_bucket{model_name=\"$model_name\"}[$__rate_interval])))",
            "fullMetaSearch": false,
            "hide": false,
            "includeNullMetadata": false,
            "instant": false,
            "legendFormat": "P95",
            "range": true,
            "refId": "B",
            "useBackend": false
            },
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "histogram_quantile(0.9, sum by(le) (rate(vllm:time_per_output_token_seconds_bucket{model_name=\"$model_name\"}[$__rate_interval])))",
            "fullMetaSearch": false,
            "hide": false,
            "includeNullMetadata": false,
            "instant": false,
            "legendFormat": "P90",
            "range": true,
            "refId": "C",
            "useBackend": false
            },
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "histogram_quantile(0.5, sum by(le) (rate(vllm:time_per_output_token_seconds_bucket{model_name=\"$model_name\"}[$__rate_interval])))",
            "fullMetaSearch": false,
            "hide": false,
            "includeNullMetadata": false,
            "instant": false,
            "legendFormat": "P50",
            "range": true,
            "refId": "D",
            "useBackend": false
            },
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "editorMode": "code",
            "expr": "rate(vllm:time_per_output_token_seconds_sum{model_name=\"$model_name\"}[$__rate_interval])\n/\nrate(vllm:time_per_output_token_seconds_count{model_name=\"$model_name\"}[$__rate_interval])",
            "hide": false,
            "instant": false,
            "legendFormat": "Mean",
            "range": true,
            "refId": "E"
            }
        ],
        "title": "Time Per Output Token Latency",
        "type": "timeseries"
        },
        {
        "datasource": {
            "type": "prometheus",
            "uid": "${DS_PROMETHEUS}"
        },
        "description": "Number of requests in RUNNING, WAITING, and SWAPPED state",
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisCenteredZero": false,
                "axisColorMode": "text",
                "axisLabel": "",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "drawStyle": "line",
                "fillOpacity": 0,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "lineInterpolation": "linear",
                "lineWidth": 1,
                "pointSize": 5,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "auto",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            },
            "unit": "none"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 12,
            "y": 8
        },
        "id": 3,
        "options": {
            "legend": {
            "calcs": [],
            "displayMode": "list",
            "placement": "bottom",
            "showLegend": true
            },
            "tooltip": {
            "mode": "single",
            "sort": "none"
            }
        },
        "targets": [
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "vllm:num_requests_running{model_name=\"$model_name\"}",
            "fullMetaSearch": false,
            "includeNullMetadata": true,
            "instant": false,
            "legendFormat": "Num Running",
            "range": true,
            "refId": "A",
            "useBackend": false
            },
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "vllm:num_requests_waiting{model_name=\"$model_name\"}",
            "fullMetaSearch": false,
            "hide": false,
            "includeNullMetadata": true,
            "instant": false,
            "legendFormat": "Num Waiting",
            "range": true,
            "refId": "C",
            "useBackend": false
            }
        ],
        "title": "Scheduler State",
        "type": "timeseries"
        },
        {
        "datasource": {
            "type": "prometheus",
            "uid": "${DS_PROMETHEUS}"
        },
        "description": "P50, P90, P95, and P99 TTFT latency in seconds.",
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisCenteredZero": false,
                "axisColorMode": "text",
                "axisLabel": "",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "drawStyle": "line",
                "fillOpacity": 0,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "lineInterpolation": "linear",
                "lineWidth": 1,
                "pointSize": 5,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "auto",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            },
            "unit": "s"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 0,
            "y": 16
        },
        "id": 5,
        "options": {
            "legend": {
            "calcs": [],
            "displayMode": "list",
            "placement": "bottom",
            "showLegend": true
            },
            "tooltip": {
            "mode": "single",
            "sort": "none"
            }
        },
        "targets": [
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "histogram_quantile(0.99, sum by(le) (rate(vllm:time_to_first_token_seconds_bucket{model_name=\"$model_name\"}[$__rate_interval])))",
            "fullMetaSearch": false,
            "hide": false,
            "includeNullMetadata": false,
            "instant": false,
            "legendFormat": "P99",
            "range": true,
            "refId": "A",
            "useBackend": false
            },
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "histogram_quantile(0.95, sum by(le) (rate(vllm:time_to_first_token_seconds_bucket{model_name=\"$model_name\"}[$__rate_interval])))",
            "fullMetaSearch": false,
            "includeNullMetadata": false,
            "instant": false,
            "legendFormat": "P95",
            "range": true,
            "refId": "B",
            "useBackend": false
            },
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "histogram_quantile(0.9, sum by(le) (rate(vllm:time_to_first_token_seconds_bucket{model_name=\"$model_name\"}[$__rate_interval])))",
            "fullMetaSearch": false,
            "hide": false,
            "includeNullMetadata": false,
            "instant": false,
            "legendFormat": "P90",
            "range": true,
            "refId": "C",
            "useBackend": false
            },
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "histogram_quantile(0.5, sum by(le) (rate(vllm:time_to_first_token_seconds_bucket{model_name=\"$model_name\"}[$__rate_interval])))",
            "fullMetaSearch": false,
            "hide": false,
            "includeNullMetadata": false,
            "instant": false,
            "legendFormat": "P50",
            "range": true,
            "refId": "D",
            "useBackend": false
            },
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "editorMode": "code",
            "expr": "rate(vllm:time_to_first_token_seconds_sum{model_name=\"$model_name\"}[$__rate_interval])\n/\nrate(vllm:time_to_first_token_seconds_count{model_name=\"$model_name\"}[$__rate_interval])",
            "hide": false,
            "instant": false,
            "legendFormat": "Average",
            "range": true,
            "refId": "E"
            }
        ],
        "title": "Time To First Token Latency",
        "type": "timeseries"
        },
        {
        "datasource": {
            "type": "prometheus",
            "uid": "${DS_PROMETHEUS}"
        },
        "description": "Percentage of used cache blocks by vLLM.",
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisCenteredZero": false,
                "axisColorMode": "text",
                "axisLabel": "",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "drawStyle": "line",
                "fillOpacity": 0,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "lineInterpolation": "linear",
                "lineWidth": 1,
                "pointSize": 5,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "auto",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green",
                    "value": null
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            },
            "unit": "percentunit"
            },
            "overrides": []
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 12,
            "y": 16
        },
        "id": 4,
        "options": {
            "legend": {
            "calcs": [],
            "displayMode": "list",
            "placement": "bottom",
            "showLegend": true
            },
            "tooltip": {
            "mode": "single",
            "sort": "none"
            }
        },
        "targets": [
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "editorMode": "code",
            "expr": "vllm:gpu_cache_usage_perc{model_name=\"$model_name\"}",
            "instant": false,
            "legendFormat": "GPU Cache Usage",
            "range": true,
            "refId": "A"
            }
        ],
        "title": "Cache Utilization",
        "type": "timeseries"
        },
        {
        "datasource": {
            "type": "prometheus",
            "uid": "${DS_PROMETHEUS}"
        },
        "description": "Heatmap of request prompt length",
        "fieldConfig": {
            "defaults": {
            "custom": {
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "scaleDistribution": {
                "type": "linear"
                }
            }
            },
            "overrides": []
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 0,
            "y": 24
        },
        "id": 12,
        "options": {
            "calculate": false,
            "cellGap": 1,
            "cellValues": {
            "unit": "none"
            },
            "color": {
            "exponent": 0.5,
            "fill": "dark-orange",
            "min": 0,
            "mode": "scheme",
            "reverse": false,
            "scale": "exponential",
            "scheme": "Spectral",
            "steps": 64
            },
            "exemplars": {
            "color": "rgba(255,0,255,0.7)"
            },
            "filterValues": {
            "le": 1e-9
            },
            "legend": {
            "show": true
            },
            "rowsFrame": {
            "layout": "auto",
            "value": "Request count"
            },
            "tooltip": {
            "mode": "single",
            "showColorScale": false,
            "yHistogram": true
            },
            "yAxis": {
            "axisLabel": "Prompt Length",
            "axisPlacement": "left",
            "reverse": false,
            "unit": "none"
            }
        },
        "pluginVersion": "11.2.0",
        "targets": [
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "sum by(le) (increase(vllm:request_prompt_tokens_bucket{model_name=\"$model_name\"}[$__rate_interval]))",
            "format": "heatmap",
            "fullMetaSearch": false,
            "includeNullMetadata": true,
            "instant": false,
            "legendFormat": "{{le}}",
            "range": true,
            "refId": "A",
            "useBackend": false
            }
        ],
        "title": "Request Prompt Length",
        "type": "heatmap"
        },
        {
        "datasource": {
            "type": "prometheus",
            "uid": "${DS_PROMETHEUS}"
        },
        "description": "Heatmap of request generation length",
        "fieldConfig": {
            "defaults": {
            "custom": {
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "scaleDistribution": {
                "type": "linear"
                }
            }
            },
            "overrides": []
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 12,
            "y": 24
        },
        "id": 13,
        "options": {
            "calculate": false,
            "cellGap": 1,
            "cellValues": {
            "unit": "none"
            },
            "color": {
            "exponent": 0.5,
            "fill": "dark-orange",
            "min": 0,
            "mode": "scheme",
            "reverse": false,
            "scale": "exponential",
            "scheme": "Spectral",
            "steps": 64
            },
            "exemplars": {
            "color": "rgba(255,0,255,0.7)"
            },
            "filterValues": {
            "le": 1e-9
            },
            "legend": {
            "show": true
            },
            "rowsFrame": {
            "layout": "auto",
            "value": "Request count"
            },
            "tooltip": {
            "mode": "single",
            "showColorScale": false,
            "yHistogram": true
            },
            "yAxis": {
            "axisLabel": "Generation Length",
            "axisPlacement": "left",
            "reverse": false,
            "unit": "none"
            }
        },
        "pluginVersion": "11.2.0",
        "targets": [
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "sum by(le) (increase(vllm:request_generation_tokens_bucket{model_name=\"$model_name\"}[$__rate_interval]))",
            "format": "heatmap",
            "fullMetaSearch": false,
            "includeNullMetadata": true,
            "instant": false,
            "legendFormat": "{{le}}",
            "range": true,
            "refId": "A",
            "useBackend": false
            }
        ],
        "title": "Request Generation Length",
        "type": "heatmap"
        },
        {
        "datasource": {
            "type": "prometheus",
            "uid": "${DS_PROMETHEUS}"
        },
        "description": "Number of finished requests by their finish reason: either an EOS token was generated or the max sequence length was reached.",
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisBorderShow": false,
                "axisCenteredZero": false,
                "axisColorMode": "text",
                "axisLabel": "",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "barWidthFactor": 0.6,
                "drawStyle": "line",
                "fillOpacity": 0,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "insertNulls": false,
                "lineInterpolation": "linear",
                "lineWidth": 1,
                "pointSize": 5,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "auto",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green"
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            }
            },
            "overrides": []
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 0,
            "y": 32
        },
        "id": 11,
        "options": {
            "legend": {
            "calcs": [],
            "displayMode": "list",
            "placement": "bottom",
            "showLegend": true
            },
            "tooltip": {
            "mode": "single",
            "sort": "none"
            }
        },
        "targets": [
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "builder",
            "expr": "sum by(finished_reason) (increase(vllm:request_success_total{model_name=\"$model_name\"}[$__rate_interval]))",
            "fullMetaSearch": false,
            "includeNullMetadata": true,
            "instant": false,
            "interval": "",
            "legendFormat": "__auto",
            "range": true,
            "refId": "A",
            "useBackend": false
            }
        ],
        "title": "Finish Reason",
        "type": "timeseries"
        },
        {
        "datasource": {
            "default": false,
            "type": "prometheus",
            "uid": "${DS_PROMETHEUS}"
        },
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisBorderShow": false,
                "axisCenteredZero": false,
                "axisColorMode": "text",
                "axisLabel": "seconds",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "barWidthFactor": 0.6,
                "drawStyle": "line",
                "fillOpacity": 0,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "insertNulls": false,
                "lineInterpolation": "linear",
                "lineWidth": 1,
                "pointSize": 5,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "auto",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green"
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            }
            },
            "overrides": []
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 12,
            "y": 32
        },
        "id": 14,
        "options": {
            "legend": {
            "calcs": [],
            "displayMode": "list",
            "placement": "bottom",
            "showLegend": true
            },
            "tooltip": {
            "mode": "single",
            "sort": "none"
            }
        },
        "targets": [
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "code",
            "expr": "rate(vllm:request_queue_time_seconds_sum{model_name=\"$model_name\"}[$__rate_interval])",
            "fullMetaSearch": false,
            "includeNullMetadata": true,
            "instant": false,
            "legendFormat": "__auto",
            "range": true,
            "refId": "A",
            "useBackend": false
            }
        ],
        "title": "Queue Time",
        "type": "timeseries"
        },
        {
        "datasource": {
            "default": false,
            "type": "prometheus",
            "uid": "${DS_PROMETHEUS}"
        },
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisBorderShow": false,
                "axisCenteredZero": false,
                "axisColorMode": "text",
                "axisLabel": "",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "barWidthFactor": 0.6,
                "drawStyle": "line",
                "fillOpacity": 0,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "insertNulls": false,
                "lineInterpolation": "linear",
                "lineWidth": 1,
                "pointSize": 5,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "auto",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green"
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            }
            },
            "overrides": []
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 0,
            "y": 40
        },
        "id": 15,
        "options": {
            "legend": {
            "calcs": [],
            "displayMode": "list",
            "placement": "bottom",
            "showLegend": true
            },
            "tooltip": {
            "mode": "single",
            "sort": "none"
            }
        },
        "targets": [
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "code",
            "expr": "rate(vllm:request_prefill_time_seconds_sum{model_name=\"$model_name\"}[$__rate_interval])",
            "fullMetaSearch": false,
            "includeNullMetadata": true,
            "instant": false,
            "legendFormat": "Prefill",
            "range": true,
            "refId": "A",
            "useBackend": false
            },
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "editorMode": "code",
            "expr": "rate(vllm:request_decode_time_seconds_sum{model_name=\"$model_name\"}[$__rate_interval])",
            "hide": false,
            "instant": false,
            "legendFormat": "Decode",
            "range": true,
            "refId": "B"
            }
        ],
        "title": "Requests Prefill and Decode Time",
        "type": "timeseries"
        },
        {
        "datasource": {
            "default": false,
            "type": "prometheus",
            "uid": "${DS_PROMETHEUS}"
        },
        "fieldConfig": {
            "defaults": {
            "color": {
                "mode": "palette-classic"
            },
            "custom": {
                "axisBorderShow": false,
                "axisCenteredZero": false,
                "axisColorMode": "text",
                "axisLabel": "",
                "axisPlacement": "auto",
                "barAlignment": 0,
                "barWidthFactor": 0.6,
                "drawStyle": "line",
                "fillOpacity": 0,
                "gradientMode": "none",
                "hideFrom": {
                "legend": false,
                "tooltip": false,
                "viz": false
                },
                "insertNulls": false,
                "lineInterpolation": "linear",
                "lineWidth": 1,
                "pointSize": 5,
                "scaleDistribution": {
                "type": "linear"
                },
                "showPoints": "auto",
                "spanNulls": false,
                "stacking": {
                "group": "A",
                "mode": "none"
                },
                "thresholdsStyle": {
                "mode": "off"
                }
            },
            "mappings": [],
            "thresholds": {
                "mode": "absolute",
                "steps": [
                {
                    "color": "green"
                },
                {
                    "color": "red",
                    "value": 80
                }
                ]
            }
            },
            "overrides": []
        },
        "gridPos": {
            "h": 8,
            "w": 12,
            "x": 12,
            "y": 40
        },
        "id": 16,
        "options": {
            "legend": {
            "calcs": [],
            "displayMode": "list",
            "placement": "bottom",
            "showLegend": true
            },
            "tooltip": {
            "mode": "single",
            "sort": "none"
            }
        },
        "targets": [
            {
            "datasource": {
                "type": "prometheus",
                "uid": "${DS_PROMETHEUS}"
            },
            "disableTextWrap": false,
            "editorMode": "code",
            "expr": "rate(vllm:request_max_num_generation_tokens_sum{model_name=\"$model_name\"}[$__rate_interval])",
            "fullMetaSearch": false,
            "includeNullMetadata": true,
            "instant": false,
            "legendFormat": "Tokens",
            "range": true,
            "refId": "A",
            "useBackend": false
            }
        ],
        "title": "Max Generation Token in Sequence Group",
        "type": "timeseries"
        }
    ],
    "refresh": "",
    "schemaVersion": 37,
    "style": "dark",
    "tags": [],
    "templating": {
        "list": [
        {
            "current": {
            "selected": false,
            "text": "Prometheus",
            "value": "Prometheus"
            },
            "hide": 0,
            "includeAll": false,
            "label": "datasource",
            "multi": false,
            "name": "DS_PROMETHEUS",
            "options": [],
            "query": "prometheus",
            "queryValue": "",
            "refresh": 1,
            "regex": "",
            "skipUrlSync": false,
            "type": "datasource"
        },
        {
            "current": {
            "isNone": true,
            "selected": false,
            "text": "None",
            "value": ""
            },
            "datasource": {
            "type": "prometheus",
            "uid": "${DS_PROMETHEUS}"
            },
            "definition": "label_values(model_name)",
            "hide": 0,
            "includeAll": false,
            "label": "model_name",
            "multi": false,
            "name": "model_name",
            "options": [],
            "query": {
            "query": "label_values(model_name)",
            "refId": "StandardVariableQuery"
            },
            "refresh": 1,
            "regex": "",
            "skipUrlSync": false,
            "sort": 0,
            "type": "query"
        }
        ]
    },
    "time": {
        "from": "now-5m",
        "to": "now"
    },
    "timepicker": {},
    "timezone": "",
    "title": "vLLM",
    "uid": "b281712d-8bff-41ef-9f3f-71ad43c05e9b",
    "version": 1,
    "weekStart": ""
    }
"""

# 这里设置了两个域名host.docker.internal和service.monitor.wang
# host.docker.internal用于表示当前所在容器所在的主机的ip. 由于node-exporter走的是host网络，因此无法通过node_exporter来访问对应的服务
# service.monitor.wang用于表示promtheus服务的域名。
# 因为otel_collector_config_template模板包含大量的{}, 为了避免过多修改，并通过{var}变量的方式替换。而是通过域名映射,在docker-compose中
# 对应域名映射ip来解决.

# 在服务器重启后似乎遇到了otel推送监控数据异常的问题, 重启启动下compose就好
otel_collector_config_template=r"""
# 接收器
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 127.0.0.1:4317
      http:
        endpoint: 127.0.0.1:4318
  hostmetrics:
    root_path: /hostfs  # 设置根目录为/hostfs, 需要docker-compose挂载根目录到该目录
    collection_interval: 30s
    scrapers:
      cpu: {}
      disk: {}
      load: {}
      filesystem: {}
      memory: {}
      network: {}
      paging: {}
     # 
     # process:
     #   mute_process_name_error: true
     #  mute_process_exe_error: true
     #  mute_process_io_error: true
      processes: {}
  prometheus:
    config:
      global:
        scrape_interval: 30s                                                                                   
      scrape_configs:
        - job_name: otel-collector-binary
          static_configs:
            - targets: ["localhost:8888"]
        # 抓取node-exporter的指标。注意hostmetrics的指标和node-exporter是完全不同的
        # grafana创建的node-exporter dashboard是无法识别hostmetrics的指标的
        - job_name: node-exporter
          static_configs:
            - targets: ["host.docker.internal:9100"]
        - job_name: dcgm-exporter
          static_configs:
            - targets: ["host.docker.internal:9400"]
# 处理器
processors:
  resource:
    attributes:
      - key: host.ip
        value: "127.0.0.1"  # 通过环境变量注入
        action: upsert
  # 批处理
  batch:
    send_batch_size: 1000
    timeout: 10s
      # 内存限制器
  memory_limiter:
    # 80% of maximum memory up to 2G
    limit_mib: 1500
    # 25% of limit up to 2G
    spike_limit_mib: 512
    check_interval: 5s
  # Ref: https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/processor/resourcedetectionprocessor/README.md
  # 资源检测
  resourcedetection:
    detectors: [env, system] # include ec2 for AWS, gcp for GCP and azure for Azure.
    # Using OTEL_RESOURCE_ATTRIBUTES envvar, env detector adds custom labels.
    timeout: 2s
    system:
      hostname_sources: [os] # alternatively, use [dns,os] for setting FQDN as host.name and os as fallback
  transform:
    metric_statements:
      - context: datapoint
        # 当指标时间为startTimestmp为0时,设置为当前时间
        # 不然Promethues会报错，并提示"out of bounds"
        statements:
          - set(start_time_unix_nano, time_unix_nano) where start_time_unix_nano == 0

extensions:
  health_check: {}
  zpages: {}

# 导出器
exporters:
  prometheus:
    endpoint: "0.0.0.0:8889"  # 暴露端口
    namespace: "otel_metrics"  # 指标前缀
    const_labels:              # 固定标签
      job: "otel-collector"
    resource_to_telemetry_conversion:
      enabled: true           # 将资源属性转为标签
    send_timestamps: true     # 包含时间戳
    metric_expiration: 10m    # 指标过期时间
  prometheusremotewrite:
    endpoint: "http://service.monitor.wang:9090/api/v1/write" # 替换为你的远程写入端点 URL
    # 通常需要认证 (例如 Basic Auth 或 Bearer Token)
    # headers:
    #  Authorization: "Bearer YOUR_REMOTE_WRITE_TOKEN" # 或 "Basic base64(user:pass)"
    # 其他可选配置:
    timeout: 30s
    retry_on_failure:
      enabled: true
      initial_interval: 5s
      max_interval: 30s
      max_elapsed_time: 300s
    tls:
      insecure: false # 生产环境应为 true，并配置 ca_file, cert_file, key_file
    resource_to_telemetry_conversion:
      enabled: true # 强烈推荐！将 OTel Resource 属性转为 Prometheus 标签
    remote_write_queue:
      enabled: true
      queue_size: 100000
      num_consumers: 50
  # 调试用的exporter
  debug:
    verbosity: detailed
    sampling_initial: 2
    sampling_thereafter: 10

# 服务启用
service:
  extensions: [health_check, zpages]
  pipelines:
    metrics:
      receivers: [prometheus, hostmetrics]
      processors: [resourcedetection, batch,transform]
      exporters: [prometheusremotewrite,debug]
"""
OTEL_COLLECTOR_CONFIG= textwrap.dedent(otel_collector_config_template).strip()

# 配置grafana预配置模板, 目的是当grafana部署好后自动配置prometheus数据源,以及相应的grafana模板
GRAFANA_PROVISIONING = {
    # textwrap.dedent：移除多行字符串中不必要的公共前导空白（缩进）
    "datasources/datasource.yml": textwrap.dedent("""\
    apiVersion: 1
    datasources:
      - name: Prometheus
        type: prometheus
        access: proxy
        url: http://prometheus:9090
        isDefault: true
        jsonData:
          timeInterval: "10s"
    """),
    
    "dashboards/dashboard.yml":textwrap.dedent("""\
    apiVersion: 1
    providers:
      - name: 'Default'
        orgId: 1
        folder: ''
        type: file
        disableDeletion: true
        updateIntervalSeconds: 10
        options:
          path: /etc/grafana/provisioning/dashboards
    """),
    
    "dashboards/dcgm-exporter-dashboard.json":  (textwrap.dedent(dcmp_exporter_dashboard)).strip(),
    "dashboards/node-exporter-single-server.json": textwrap.dedent(node_single_server_dashboard).strip(),
    "dashboards/named-process.json": textwrap.dedent(named_process_dashboard).strip(),
    "dashboards/system-process.json": textwrap.dedent(system_process_metrics_dashboard).strip(),
    "dashboards/vllm-dashboard.json": textwrap.dedent(vllm_dashboard).strip(),
}

DOCKER_COMPOSE_BOOTUP_SERVICE="""
[Unit]
Description=Docker Compose Application Service
Requires=docker.service
After=docker.service network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory={workDir}  
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
"""

MONITOR_SERVER_DOCKER_COMPOSE_TEMPLATE = textwrap.dedent("""\
version: '3'
services:
  prometheus:
    image: "{prometheusImage}"
    ports:
      - "9090:9090"
    volumes:
      - {monitorStackDir}/prometheus.yml:/etc/prometheus/prometheus.yml                                            
      - {monitorStackDir}/prometheus_data:/prometheus                                       
    command: 
      - --config.file=/etc/prometheus/prometheus.yml
      - --web.enable-remote-write-receiver       
    restart: unless-stopped

  grafana:
    image: {grafanaImage}
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD={admin_password}
    volumes:
      # grafana预配置比如datasource, dashboard等。但注意设置后无法通过页面修改这些dashboard
      - {monitorStackDir}/grafana/provisioning:/etc/grafana/provisioning
      # 将grafana sqlite数据挂载到主机目录, 避免重启后grafana数据丢失 
      # 注意主机挂载目录必须设置为0777                                                
      - {monitorStackDir}/grafana/data:/var/lib/grafana
    restart: unless-stopped
""")

AGENT_DOCKER_COMPOSE_TEMPLATE = textwrap.dedent("""\
version: '3'
# 添加域名映射, 通过`docker inspect`可以看到`ExtraHosts`
x-host-mappings: &host-mappings
  extra_hosts:
    - "service.monitor.wang:{monitorServiceIp}"
    - "host.docker.internal:{localHostIp}"
                                                                                              
services:
  otel-collector:
    image: "{otelCollectorImage}"
    ports:
      - 127.0.0.1:1888:1888 # pprof extension
      - 8888:8888 # otel collector自身的指标
      - 8889:8889 # otel 接收到并处理后的prometheus指标
      - 13133:13133 # health_check extension
      - 127.0.0.1:4317:4317 # OTLP gRPC receiver
      - 127.0.0.1:4318:4318 # OTLP http receiver
    volumes:
      - {monitorAgentDir}/otel-collector-config.yaml:/etc/otelcol-contrib/config.yaml
      - /:/hostfs:ro,rslave
      - /etc/localtime:/etc/localtime:ro                                         
      - /etc/timezone:/etc/timezone:ro
      - /etc/passwd:/etc/passwd:ro
      - /etc/group:/etc/group                                          
    restart: unless-stopped
    <<: *host-mappings                                         
  node_exporter:
    image: {nodeExporterImage}
    container_name: node_exporter
    command:
      - '--path.rootfs=/host'
    network_mode: host
    pid: host
    restart: unless-stopped
    volumes:
      - '/:/host:ro,rslave'                                              
      - /etc/localtime:/etc/localtime:ro
      - /etc/timezone:/etc/timezone:ro

  dcgm-exporter:
    image: {nvidiaDcgmExporterImage}
    # 必须安装nvidia-container-toolkit
    runtime: nvidia
    # 设置以监听服务进程 gpu使用情况                                            
    pid: host
    ports:
     # - 127.0.0.1:9400:9400
      - 9400:9400
    # 挂载全部gpu
    # https://docs.docker.com/compose/how-tos/gpu-support/                                           
    deploy:
      resources:
        reservations:
          devices:
          - driver: nvidia
            count: all
            capabilities: [gpu]                                                                                                                                  
    cap_add:
      - SYS_ADMIN
      - IPC_LOCK  # 通常与 SYS_ADMIN 配合使用                                                                                             
    environment:
      - NVIDIA_VISIBLE_DEVICES=all  # 允许访问所有 GPU
    restart: unless-stopped
""")

PROMETHEUS_TEMPLATE= textwrap.dedent(prometheus_config_template).strip()

def get_local_ip():
    """自动获取本机有效IPv4地址（非回环地址）"""
    try:
        # 方法1：通过UDP连接获取真实网络IP（推荐）
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))  # 连接公共DNS
            return s.getsockname()[0]
    except Exception as e:
        print(f"UDP method failed: {str(e)}, trying fallback method")
    
    try:
        # 方法2：通过主机名解析（备用）
        hostname = socket.gethostname()
        ip_list = socket.gethostbyname_ex(hostname)[2]
        # 过滤回环地址和IPv6
        valid_ips = [ip for ip in ip_list if not ip.startswith("127.") and '.' in ip]
        return valid_ips[0] if valid_ips else "127.0.0.1"
    except Exception as e:
        print(f"All IP detection methods failed: {str(e)}")
        return "127.0.0.1"  # 终极回退

def generate_config_files(output_dir: str, config_map: dict):
    """
    根据配置映射生成多个配置文件
    :param output_dir: 输出目录
    :param config_map: 配置映射字典 {文件名: 文件内容}
    """
    os.makedirs(output_dir, exist_ok=True)
    
    for filename, content in config_map.items():
        file_path = os.path.join(output_dir, filename)
        with open(file_path, "w") as f:
            f.write(content)
        print(f"✅ 已生成配置文件: {file_path}")

def stack_generate(args):
    """根据参数生成服务栈或代理栈的Docker配置"""
    if args.type == 'service':
        service_stack_generate(args)
    elif args.type == 'agent':
        agent_stack_generate(args)
    else:
        print(f"⚠️ 无效的服务类型: {args.type}. 只支持 'service' 或 'agent'")
        raise ValueError(f"无效的服务类型: {args.type}")

def service_stack_generate(args):
    """生成docker-compose.yaml文件"""
    if args.password:
        # 如果命令行提供了密码参数，则覆盖默认密码
        print(f"已使用命令行提供的密码覆盖默认密码")
        grafanaPassword = args.password
    compose_content = MONITOR_SERVER_DOCKER_COMPOSE_TEMPLATE.format(
        admin_password=grafanaPassword,
        monitorStackDir=monitorStackDir,
        prometheusImage=prometheusImage,
        grafanaImage=grafanaImage,
        )
    
    os.makedirs(monitorStackDir,exist_ok=True)

    with open(monitorStackDir+"/docker-compose.yaml", "w") as f:
        f.write(compose_content)
    
    # 创建Prometheus基础配置
    with open(monitorStackDir+"/prometheus.yml", "w") as f:
        f.write(PROMETHEUS_TEMPLATE.format())
    print(f"✅ {monitorStackDir}/docker-compose.yaml 和 {monitorStackDir}/prometheus.yml 已生成")

    generate_provisioning(args)

    service_docker_compose_bootup=SystemdService("monitor-service-compose-bootup")
    service_docker_compose_bootup.generate_config(
        exec_start="/usr/bin/docker compose up -d --remove-orphans",
        description="service-compose-bootup",
        working_directory=monitorStackDir,
        wanted_by="wanted_by.service docker.service",
        service_type="oneshot",
        restart_policy="no",
    )



def agent_stack_generate(args):
    """生成docker-compose.yaml文件"""
    os.makedirs(monitorAgentDir,exist_ok=True)

    config_map = {
        "docker-compose.yaml": AGENT_DOCKER_COMPOSE_TEMPLATE.format(
            monitorAgentDir=monitorAgentDir,
            otelCollectorImage=otelCollectorImage,
            nvidiaDcgmExporterImage=nvidiaDcgmExporterImage,
            nodeExporterImage=nodeExporterImage,
            monitorServiceIp=args.monitor_service_ip,
            localHostIp=get_local_ip(),
            ),
        "otel-collector-config.yaml": OTEL_COLLECTOR_CONFIG,
    }
    generate_config_files(monitorAgentDir, config_map)
    print("所有代理配置文件已生成完毕")

    # node_exporter_service=SystemdService("node-exporter")
    # node_exporter_service.generate_config(
    #     exec_start="/usr/local/bin/node-exporter --web.listen-address 127.0.0.1:8081 --collector.textfile.directory /etc/prometheus/node-exporter/scripts",
    #     description="node-exporter",
    #     working_directory="/etc/prometheus",
    # )

    agent_docker_compose_bootup=SystemdService("monitor-agent-compose-bootup")
    agent_docker_compose_bootup.generate_config(
        exec_start="/usr/bin/docker compose up -d --remove-orphans",
        description="agent-compose-bootup",
        working_directory=monitorAgentDir,
        wanted_by="wanted_by.service docker.service",
        service_type="oneshot",
        restart_policy="no",
    )

# 检查服务是否已运行
def is_service_running(args):
    """判断服务栈是否运行"""
    if args.type == 'service':
        stackDir=monitorStackDir
    elif args.type == 'agent':
        stackDir=monitorAgentDir
    else:
        print(f"⚠️ 无效的服务类型: {args.type}. 只支持 'service' 或 'agent'")
        raise ValueError(f"无效的服务类型: {args.type}")
    
    try:
        check_cmd = ["docker","compose", "ps", "--services", "--filter", "status=running"]
        result = subprocess.run(
            check_cmd, 
            cwd=stackDir,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=True
        )
        return result.stdout.strip() != ""
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
    
def start_stack(args):
    """根据参数启动服务栈或代理栈的Docker配置"""
    if args.type == 'service':
        service_run_stack(args)
    elif args.type == 'agent':
        agent_run_stack(args)
    else:
        print(f"⚠️ 无效的服务类型: {args.type}. 只支持 'service' 或 'agent'")
        raise ValueError(f"无效的服务类型: {args.type}")

def service_run_stack(args):
    """启动Docker Compose服务"""
    if not Path(f"{monitorStackDir}/docker-compose.yaml").exists():
        print(f"❌ 错误：未找到 {monitorStackDir}/docker-compose.yaml 文件, 执行'python <script>.py stack'生成")
        sys.exit(1)
    
    if not Path(f"{grafanaProvisionDir}").exists():
        print(f"❌ 错误：未找到 {grafanaProvisionDir} 目录，执行'python <script>.py provision'生成")
        sys.exit(1)
    
    # 检查prometheus目录是否存在
    prometheus_data_path=f"{monitorStackDir}/prometheus_data"
    if not Path(f"{prometheus_data_path}").exists():
        print(f"⚠️  Prometheus数据目录不存在，创建: {prometheus_data_path}")
        os.makedirs(prometheus_data_path, mode=0o777,exist_ok=True)
        os.chmod(prometheus_data_path,mode=0o777)
    else:
        os.chmod(prometheus_data_path,mode=0o777)
        # 必须设置为0777, 否则prometheus尝试创建数据目录会失败
        print(f"⚠️  Prometheus数据目录 {prometheus_data_path}已存在，设置为0777")

    # 检查grafana目录是否存在
    grafana_data_path=f"{monitorStackDir}/grafana/data"
    if not Path(f"{grafana_data_path}").exists():
        print(f"⚠️  Grafana数据目录不存在，创建: {grafana_data_path}")
        os.makedirs(grafana_data_path, mode=0o777,exist_ok=True)
        # 必须要设置为0777, 才能让grafana, prometheus写入数据
        os.chmod(grafana_data_path,mode=0o777)
    else:
        os.chmod(grafana_data_path,mode=0o777)
        # 必须设置为0777, 否则prometheus尝试创建数据目录会失败
        print(f"⚠️  Grafana数据目录 {grafana_data_path}已存在，设置为0777")

    # 检测并停止运行中的服务
    if is_service_running(args):
        print("🔴 检测到服务正在运行，准备重启...")
        stop_cmd = ["docker","compose", "down"]
        subprocess.run(stop_cmd, check=True, cwd=monitorStackDir)
        print("🛑 已停止运行中的服务")

    cmd = ["docker","compose", "up", "-d"]
    if args.build:
        cmd.insert(1, "--build")
    
    try:
        print(f"🏗️  在目录 {monitorStackDir} 中启动服务...")
        # 切换到指定目录来执行
        result = subprocess.run(cmd, check=True, cwd=monitorStackDir)
        print("\n🎉 服务已启动")
        print(grafanaPassword)
        print(f"Grafana: http://localhost:3000 (用户为admin )")
        print("Prometheus: http://localhost:9090")
    except subprocess.CalledProcessError as e:
        print(f"❌ 启动失败: {e}")
        sys.exit(1)

    service_docker_compose_bootup=SystemdService("monitor-service-compose-bootup")
    if service_docker_compose_bootup.is_active() == False:
        service_docker_compose_bootup.start()

def agent_run_stack(args):
    """启动Docker Compose服务"""
    if not Path(f"{monitorAgentDir}/docker-compose.yaml").exists():
        print(f"❌ 错误：未找到 {monitorAgentDir}/docker-compose.yaml 文件, 执行'python <script>.py stack -t agent'生成")
        sys.exit(1)

    # 检测并停止运行中的服务
    if is_service_running(args):
        print("🔴 检测到服务正在运行，准备重启...")
        stop_cmd = ["docker","compose", "down"]
        subprocess.run(stop_cmd, check=True, cwd=monitorAgentDir)
        print("🛑 已停止运行中的服务")

    cmd = ["docker","compose", "up", "-d"]
    if args.build:
        cmd.insert(1, "--build")
    
    try:
        print(f"🏗️  在目录 {monitorAgentDir} 中启动服务...")
        # 切换到指定目录来执行
        result = subprocess.run(cmd, check=True, cwd=monitorAgentDir)
        print("\n🎉 服务已启动")
    except subprocess.CalledProcessError as e:
        print(f"❌ 启动失败: {e}")
        sys.exit(1)

    # node_exporter_service=SystemdService("node-exporter")
    # if node_exporter_service.is_active() == False:
    #     node_exporter_service.start()
    
    service_docker_compose_bootup=SystemdService("monitor-agent-compose-bootup")
    if service_docker_compose_bootup.is_active() == False:
        service_docker_compose_bootup.start()

def stop_stack(args):
    """停止Docker Compose服务"""
    if args.type == 'service':
        stackDir=monitorStackDir
    elif args.type == 'agent':
        stackDir=monitorAgentDir
    else:
        print(f"⚠️ 无效的服务类型: {args.type}. 只支持 'service' 或 'agent'")
        raise ValueError(f"无效的服务类型: {args.type}")
    if not Path(f"{stackDir}/docker-compose.yaml").exists():
        print(f"❌ 错误：未找到 {stackDir}/docker-compose.yaml 文件")
        sys.exit(1)
    
    cmd = ["docker","compose", "down", ]
    try:
        print(f"🏗️  在目录 {stackDir} 中停止服务...")
        # 切换到指定目录来执行
        result = subprocess.run(cmd, check=True, cwd=stackDir)
        print("\n🎉 服务已停止")
    except subprocess.CalledProcessError as e:
        print(f"❌ 服务停止失败: {e}")
        sys.exit(1)

    # node_exporter_service=SystemdService("node-exporter")
    # if node_exporter_service.is_active() == True:
    #     node_exporter_service.stop_and_disable()    

    service_docker_compose_bootup=SystemdService("monitor-service-compose-bootup")
    if service_docker_compose_bootup.is_active() == True:
        service_docker_compose_bootup.stop_and_disable()

def generate_provisioning(args):
    """创建Grafana配置目录结构"""
    base_path = Path(grafanaProvisionDir)
    
    # 创建目录结构并写入文件
    for rel_path, content in GRAFANA_PROVISIONING.items():
        full_path = base_path / rel_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content)
        print(f"📝 已创建: {full_path}")
    
    print("\n✅ Grafana配置已生成在 grafana/provisioning 目录")
    print("启动服务后会自动加载配置")
    
def print_example():
    print("="*60)
    print("功能示例".center(60))
    print("="*60)
    print("1. 生成监控服务端或者Agent端 docker-compose")
    print(" "*10 +  "服务端：python monitor.py stack -t service")
    print(" "*10 +  "Agent端: python monitor.py stack -t agent")

    print("2. 启动监控服务端或者Agent端")
    print(" "*10 +  "服务端：python monitor.py start -t service")
    print(" "*10 +  "Agent端: python monitor.py start -t agent")

    print("3. 停止监控服务端或者Agent端")
    print(" "*10 +  "服务端：python monitor.py stop -t service")
    print(" "*10 +  "Agent端: python monitor.py stop -t agent")

def main():
    parser = argparse.ArgumentParser(description="ai监控单点部署工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # stack命令
    stack_parser = subparsers.add_parser("stack", help="生成docker-compose.yaml")
    stack_parser.add_argument("-p", "--password", help="设置Grafana管理员密码",default=grafanaPassword)
    stack_parser.add_argument("--monitor-service-ip", help="设置监控服务ip",default=get_local_ip())
    stack_parser.add_argument("-t", "--type", help="部署agent还是service")
    stack_parser.set_defaults(func=stack_generate)

    # run命令
    run_parser = subparsers.add_parser("start", help="启动监控服务")
    run_parser.add_argument("--build", action="store_true", help="重新构建镜像")
    run_parser.add_argument("-t", "--type", help="运行agent还是service")
    run_parser.set_defaults(func=start_stack)

    # stop命令
    stop_parser = subparsers.add_parser("stop", help="停止")
    stop_parser.add_argument("-t", "--type", help="运行agent还是service")
    stop_parser.set_defaults(func=stop_stack)

    # provision命令
    prov_parser = subparsers.add_parser("provision", help="生成Grafana配置")
    prov_parser.set_defaults(func=generate_provisioning)

    # example命令
    example_parser = subparsers.add_parser("example", help="代码示例")
    example_parser.set_defaults(func=print_example)
  
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
