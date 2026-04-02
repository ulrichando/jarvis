packer {
  required_plugins {
    virtualbox = {
      version = ">= 1.1.0"
      source  = "github.com/hashicorp/virtualbox"
    }
  }
}

variable "iso_url" {
  type    = string
  default = "https://cdimage.debian.org/debian-cd/current/amd64/iso-cd/debian-13.4.0-amd64-netinst.iso"
}

variable "iso_checksum" {
  type    = string
  default = "sha256:0b813535dd76f2ea96eff908c65e8521512c92a0631fd41c95756ffd7d4896dc"
}

variable "cpus" {
  type    = number
  default = 4
}

variable "memory" {
  type    = number
  default = 4096
}

variable "disk_size" {
  type    = number
  default = 40000
}

source "virtualbox-iso" "jarvis-os" {
  guest_os_type    = "Debian_64"
  iso_url          = var.iso_url
  iso_checksum     = var.iso_checksum

  cpus             = var.cpus
  memory           = var.memory
  disk_size        = var.disk_size

  ssh_username     = "jarvis"
  ssh_password     = "jarvis"
  ssh_timeout      = "30m"

  http_directory   = "http"

  boot_command = [
    "<esc><wait>",
    "auto url=http://{{ .HTTPIP }}:{{ .HTTPPort }}/preseed.cfg ",
    "hostname=jarvis domain=local ",
    "fb=false debconf/frontend=noninteractive ",
    "console-setup/ask_detect=false ",
    "<enter>"
  ]

  vboxmanage = [
    ["modifyvm", "{{.Name}}", "--vram", "128"],
    ["modifyvm", "{{.Name}}", "--graphicscontroller", "vmsvga"],
    ["modifyvm", "{{.Name}}", "--audio-driver", "pulse"],
    ["modifyvm", "{{.Name}}", "--audio-enabled", "on"],
    ["modifyvm", "{{.Name}}", "--audio-out", "on"],
    ["modifyvm", "{{.Name}}", "--audio-in", "on"],
    ["modifyvm", "{{.Name}}", "--nat-pf1", "jarvis-web,tcp,,8765,,8765"],
    ["modifyvm", "{{.Name}}", "--nat-pf1", "jarvis-grpc,tcp,,50051,,50051"],
    ["modifyvm", "{{.Name}}", "--clipboard-mode", "bidirectional"],
    ["modifyvm", "{{.Name}}", "--draganddrop", "bidirectional"],
  ]

  shutdown_command  = "echo 'jarvis' | sudo -S shutdown -P now"
  output_directory  = "output-jarvis-os"
  format            = "ova"
  vm_name           = "JARVIS-OS"
}

build {
  sources = ["source.virtualbox-iso.jarvis-os"]

  # Copy JARVIS application into the VM
  provisioner "file" {
    source      = "../../"
    destination = "/tmp/jarvis-src"
  }

  # Copy CogScript into the VM
  provisioner "file" {
    source      = "../../../CogScript/"
    destination = "/tmp/cogscript-src"
  }

  # Copy OS configs (systemd, sway, waybar, plymouth, grub)
  provisioner "file" {
    source      = "../systemd/"
    destination = "/tmp/jarvis-systemd"
  }

  provisioner "file" {
    source      = "../sway/"
    destination = "/tmp/jarvis-sway"
  }

  provisioner "file" {
    source      = "../waybar/"
    destination = "/tmp/jarvis-waybar"
  }

  provisioner "file" {
    source      = "../plymouth/"
    destination = "/tmp/jarvis-plymouth"
  }

  provisioner "file" {
    source      = "../grub/"
    destination = "/tmp/jarvis-grub"
  }

  provisioner "file" {
    source      = "../assets/"
    destination = "/tmp/jarvis-assets"
  }

  # Run provisioning scripts in order
  provisioner "shell" {
    scripts = [
      "scripts/00-base.sh",
      "scripts/01-desktop.sh",
      "scripts/02-audio.sh",
      "scripts/03-python.sh",
      "scripts/04-rust.sh",
      "scripts/05-jarvis-install.sh",
      "scripts/06-services.sh",
      "scripts/07-gui.sh",
      "scripts/08-boot.sh",
      "scripts/09-vbox-guest.sh",
      "scripts/10-cleanup.sh",
    ]
    execute_command = "echo 'jarvis' | sudo -S bash '{{ .Path }}'"
  }
}
