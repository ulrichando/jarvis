package com.jarvis.android.presentation.network

import android.content.Context
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.net.wifi.ScanResult
import android.net.wifi.WifiManager
import android.os.Build
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.jarvis.android.domain.usecase.ExecuteCommandUseCase
import dagger.hilt.android.lifecycle.HiltViewModel
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

data class WifiNetwork(
    val ssid:       String,
    val bssid:      String,
    val rssi:       Int,         // dBm
    val frequency:  Int,         // MHz
    val security:   String,
    val channel:    Int,
)

data class NetworkUiState(
    val isConnected:  Boolean           = false,
    val transport:    String            = "none",  // wifi / cellular / ethernet / vpn
    val ssid:         String            = "",
    val ipv4:         String            = "",
    val ipv6:         String            = "",
    val networks:     List<WifiNetwork> = emptyList(),
    val routeOutput:  String            = "",
    val isScanning:   Boolean           = false,
    val error:        String?           = null,
)

sealed class NetworkIntent {
    object Refresh     : NetworkIntent()
    object Scan        : NetworkIntent()
    object RunRoute    : NetworkIntent()
    object ClearError  : NetworkIntent()
}

@HiltViewModel
class NetworkViewModel @Inject constructor(
    @ApplicationContext private val context: Context,
    private val executeCommand: ExecuteCommandUseCase,
) : ViewModel() {

    private val _uiState = MutableStateFlow(NetworkUiState())
    val uiState: StateFlow<NetworkUiState> = _uiState.asStateFlow()

    private val wifiManager: WifiManager by lazy {
        context.getSystemService(Context.WIFI_SERVICE) as WifiManager
    }
    private val connectivityManager: ConnectivityManager by lazy {
        context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
    }

    init { refresh() }

    fun onIntent(intent: NetworkIntent) = when (intent) {
        is NetworkIntent.Refresh    -> refresh()
        is NetworkIntent.Scan       -> scanWifi()
        is NetworkIntent.RunRoute   -> runRoute()
        is NetworkIntent.ClearError -> _uiState.update { it.copy(error = null) }
    }

    private fun refresh() {
        viewModelScope.launch(Dispatchers.IO) {
            val (connected, transport) = readConnectivity()
            val (ssid, ipv4, ipv6)    = readWifiDetails()
            _uiState.update {
                it.copy(
                    isConnected = connected,
                    transport   = transport,
                    ssid        = ssid,
                    ipv4        = ipv4,
                    ipv6        = ipv6,
                )
            }
        }
    }

    private fun readConnectivity(): Pair<Boolean, String> {
        val network = connectivityManager.activeNetwork
            ?: return false to "none"
        val caps = connectivityManager.getNetworkCapabilities(network)
            ?: return false to "none"
        val transport = when {
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)     -> "wifi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN)      -> "vpn"
            else                                                       -> "other"
        }
        return caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) to transport
    }

    @Suppress("DEPRECATION")
    private fun readWifiDetails(): Triple<String, String, String> {
        val info = wifiManager.connectionInfo
        val rawSsid = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            // On API 33+ ssid requires NEARBY_WIFI_DEVICES or fine location
            info.ssid?.removePrefix("\"")?.removeSuffix("\"") ?: ""
        } else {
            info.ssid?.removePrefix("\"")?.removeSuffix("\"") ?: ""
        }
        val ipInt = info.ipAddress
        val ipv4 = "%d.%d.%d.%d".format(
            ipInt and 0xff,
            (ipInt shr 8) and 0xff,
            (ipInt shr 16) and 0xff,
            (ipInt shr 24) and 0xff,
        ).takeIf { ipInt != 0 } ?: ""
        return Triple(rawSsid, ipv4, "")
    }

    private fun scanWifi() {
        viewModelScope.launch(Dispatchers.IO) {
            _uiState.update { it.copy(isScanning = true) }
            @Suppress("DEPRECATION")
            wifiManager.startScan()
            @Suppress("DEPRECATION")
            val results: List<ScanResult> = wifiManager.scanResults ?: emptyList()
            val networks = results
                .sortedByDescending { it.level }
                .map { r ->
                    WifiNetwork(
                        ssid      = r.SSID,
                        bssid     = r.BSSID,
                        rssi      = r.level,
                        frequency = r.frequency,
                        security  = parseSecurity(r.capabilities),
                        channel   = frequencyToChannel(r.frequency),
                    )
                }
            _uiState.update { it.copy(networks = networks, isScanning = false) }
        }
    }

    private fun runRoute() {
        viewModelScope.launch(Dispatchers.IO) {
            runCatching { executeCommand("ip route", asRoot = false) }
                .onSuccess { out -> _uiState.update { it.copy(routeOutput = out) } }
                .onFailure { e  -> _uiState.update { it.copy(error = e.message) } }
        }
    }

    private fun parseSecurity(capabilities: String): String = when {
        capabilities.contains("WPA3") -> "WPA3"
        capabilities.contains("WPA2") -> "WPA2"
        capabilities.contains("WPA")  -> "WPA"
        capabilities.contains("WEP")  -> "WEP"
        else                           -> "Open"
    }

    private fun frequencyToChannel(frequency: Int): Int = when {
        frequency in 2412..2484 -> (frequency - 2407) / 5
        frequency in 5180..5825 -> (frequency - 5000) / 5
        else                     -> 0
    }
}
