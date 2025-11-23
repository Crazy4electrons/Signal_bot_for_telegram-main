#!/usr/bin/env python3
"""
measure_latency.py

Simple utility to measure DNS lookup, TCP connect, and HTTP GET latency for one or more
hosts/URLs. No external dependencies.

Usage:
  python measure_latency.py --hosts google.com https://example.com 8.8.8.8:53 --count 3

"""
import argparse
import socket
import time
import urllib.request
import ssl
from urllib.parse import urlparse


def timed(fn, *args, **kwargs):
    start = time.perf_counter()
    result = fn(*args, **kwargs)
    end = time.perf_counter()
    return (end - start, result)


def dns_lookup(host):
    # measure time for resolving host to addresses
    def _resolve(h):
        return socket.getaddrinfo(h, None)

    try:
        duration, addrs = timed(_resolve, host)
        return duration, len(addrs)
    except Exception as e:
        return None, str(e)


def tcp_connect(host, port, timeout=3.0):
    def _connect(h, p):
        with socket.create_connection((h, p), timeout=timeout) as s:
            # if TLS is requested by caller, they'll wrap separately; here we just connect
            return True

    try:
        duration, _ = timed(_connect, host, port)
        return duration
    except Exception as e:
        return None


def http_get(url, timeout=5.0):
    # perform a simple GET and measure time to first byte and total
    parsed = urlparse(url)
    if not parsed.scheme:
        url = "http://" + url

    req = urllib.request.Request(url, headers={"User-Agent": "measure-latency/1.0"})
    ctx = None
    if url.startswith("https://"):
        ctx = ssl.create_default_context()
    try:
        start = time.perf_counter()
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            first_byte = time.perf_counter()
            body = resp.read(1024)
            end = time.perf_counter()
            return {
                "status": resp.getcode(),
                "first_byte_ms": (first_byte - start) * 1000.0,
                "total_ms": (end - start) * 1000.0,
                "bytes_read": len(body),
            }
    except Exception as e:
        return {"error": str(e)}


def parse_host_token(token):
    # token can be: host, host:port, http://..., https://...
    if token.startswith("http://") or token.startswith("https://"):
        return ("http", token)
    if ":" in token:
        host, port = token.rsplit(":", 1)
        try:
            p = int(port)
            return ("tcp", (host, p))
        except ValueError:
            return ("host", token)
    # bare IP or hostname
    return ("host", token)


def measure_one(token):
    kind, payload = parse_host_token(token)
    out = {"target": token}
    if kind == "http":
        out["http"] = http_get(payload)
    elif kind == "tcp":
        host, port = payload
        # attempt DNS then TCP
        dns = dns_lookup(host)
        tcp = tcp_connect(host, port)
        out["dns_ms"] = dns[0] * 1000.0 if isinstance(dns[0], float) else dns[0]
        out["tcp_ms"] = tcp * 1000.0 if isinstance(tcp, float) else tcp
    else:
        # try DNS, then TCP to common ports (80 and 443) if available
        dns = dns_lookup(payload)
        out["dns_ms"] = dns[0] * 1000.0 if isinstance(dns[0], float) else dns[0]
    return out


def main():
    p = argparse.ArgumentParser(description="Measure simple latencies (DNS, TCP, HTTP)")
    p.add_argument("--hosts", "-H", nargs="+", help="Hosts, host:port, or full URLs to test",
                   default=["https://example.com", "google.com", "8.8.8.8:53"]) 
    p.add_argument("--count", "-c", type=int, default=1, help="Number of times to run each test")
    args = p.parse_args()

    for token in args.hosts:
        print(f"== {token} ==")
        for i in range(args.count):
            res = measure_one(token)
            # pretty print minimal
            if "http" in res:
                http = res["http"]
                if "error" in http:
                    print(f"  HTTP error: {http['error']}")
                else:
                    print(f"  HTTP status={http['status']} first_byte={http['first_byte_ms']:.1f}ms total={http['total_ms']:.1f}ms bytes={http['bytes_read']}")
            else:
                dns = res.get("dns_ms")
                tcp = res.get("tcp_ms")
                if dns is None:
                    print(f"  DNS error: {res.get('dns_ms')}")
                else:
                    print(f"  DNS: {dns:.1f} ms")
                if tcp is not None:
                    if isinstance(tcp, float):
                        print(f"  TCP connect: {tcp:.1f} ms")
                    else:
                        print(f"  TCP error")
        print()


if __name__ == "__main__":
    main()
