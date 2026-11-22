export const CODE_TEMPLATES: Record<string, string> = {
  cpp: `#include <iostream>
#include <vector>

class Solution {
public:
    int solve() {
        std::vector<int> nums = {1, 2, 3, 4, 5};
        int sum = 0;
        for (int n : nums) sum += n;
        return sum;
    }
};

int main() {
    Solution s;
    std::cout << "Sum: " << s.solve() << std::endl;
    return 0;
}
`,

  java: `public class Solution {
    public static void main(String[] args) {
        System.out.println("Hello from Java OpenJDK in PolyExecution Studio 2");
    }
}
`,

  python3: `def solve():
    nums = [1, 2, 3, 4, 5]
    return sum(nums)

if __name__ == "__main__":
    print(f"Result: {solve()}")
`,

  python2: `# Legacy Python 2
print "Legacy Python 2 execution"
`,

  c: `#include <stdio.h>

int main() {
    printf("Hello from C (gcc) in PolyExecution Studio 2\\n");
    return 0;
}
`,

  csharp: `using System;

public class Solution {
    public static void Main(string[] args) {
        Console.WriteLine("Hello from C# (.NET 10 CoreCLR)");
    }
}
`,

  javascript: `function solve() {
    const nums = [1, 2, 3, 4, 5];
    return nums.reduce((acc, curr) => acc + curr, 0);
}

console.log("Sum:", solve());
`,

  typescript: `function twoSum(nums: number[], target: number): number[] {
    const map = new Map<number, number>();
    for (let i = 0; i < nums.length; i++) {
        const complement = target - nums[i];
        if (map.has(complement)) {
            return [map.get(complement)!, i];
        }
        map.set(nums[i], i);
    }
    return [];
}

const result = twoSum([2, 7, 11, 15], 9);
console.log("Indices:", result);
`,

  ruby: `def solve
  nums = [1, 2, 3, 4, 5]
  nums.sum
end

puts "Result: #{solve}"
`,

  bash: `#!/usr/bin/env bash
echo "Hello from isolated Bash subshell"
echo "Current directory: $(pwd)"
`,

  php: `<?php
function solve() {
    $nums = [1, 2, 3, 4, 5];
    return array_sum($nums);
}

echo "Result: " . solve() . "\\n";
?>
`,

  sqlite: `-- Disposable Ephemeral SQLite Sandbox
CREATE TABLE employees (
    id INTEGER PRIMARY KEY,
    name TEXT,
    salary INTEGER,
    department TEXT
);

INSERT INTO employees VALUES
(1, 'Alice', 95000, 'Engineering'),
(2, 'Bob', 82000, 'Engineering'),
(3, 'Charlie', 75000, 'Marketing');

SELECT department, AVG(salary) as avg_salary, COUNT(*) as headcount
FROM employees
GROUP BY department;
`,

  pandas: `# Pre-imported: pd (pandas), np (numpy)
data = {
    'employee_id': [1, 2, 3, 4],
    'name': ['Alice', 'Bob', 'Charlie', 'David'],
    'salary': [70000, 85000, 92000, 65000],
    'department': ['IT', 'HR', 'IT', 'Marketing']
}

df = pd.DataFrame(data)

# Result can be stored in 'result', 'df', or returned from 'solve()'
result = df.groupby('department')['salary'].mean().reset_index()
result.columns = ['department', 'average_salary']
`,

  react: `import React, { useState } from 'react';

export default function App() {
    const [count, setCount] = useState(0);

    return (
        <div style={{ padding: '24px', fontFamily: 'sans-serif' }}>
            <h2>React 18 Component Sandbox</h2>
            <p>Count: <strong>{count}</strong></p>
            <button 
                onClick={() => setCount(c => c + 1)}
                style={{
                    padding: '8px 16px',
                    backgroundColor: '#2563eb',
                    color: 'white',
                    border: 'none',
                    borderRadius: '4px',
                    cursor: 'pointer'
                }}
            >
                Increment Counter
            </button>
        </div>
    );
}
`,

  vanilla_js: `// Isolated Client DOM Sandbox
const container = document.getElementById('root');

const card = document.createElement('div');
card.style.cssText = 'padding: 20px; background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;';

const title = document.createElement('h3');
title.textContent = 'Vanilla JavaScript DOM Workspace';
card.appendChild(title);

const p = document.createElement('p');
p.textContent = 'Executed inside an isolated iframe sandbox with zero host DOM access.';
card.appendChild(p);

container.appendChild(card);
console.log('DOM component rendered successfully');
`,

  dart: `void main() {
    print('Hello from Dart in PolyExecution Studio 2');
}
`,

  racket: `#lang racket
(displayln "Hello from Racket Chez Scheme")
`,

  erlang: `-module(solution).
-export([start/0]).

start() ->
    io:format("Hello from Erlang OTP 29~n").
`,

  elixir: `defmodule Solution do
    def main do
        IO.puts("Hello from Elixir 1.20")
    end
end

Solution.main()
`,

  swift: `print("Hello from Swift")
`,

  go: `package main
import "fmt"

func main() {
    fmt.Println("Hello from Go")
}
`,

  rust: `fn main() {
    println!("Hello from Rust");
}
`,

  kotlin: `fun main() {
    println("Hello from Kotlin")
}
`,

  scala: `object Solution {
    def main(args: Array[String]): Unit = {
        println("Hello from Scala")
    }
}
`,

  postgresql: `-- PostgreSQL query
SELECT 1 as id, 'PostgreSQL' as name;
`,

  mysql: `-- MySQL query
SELECT 1 as id, 'MySQL' as name;
`,

  mssql: `-- MS SQL Server query
SELECT 1 as id, 'MSSQL' as name;
`,

  oracle: `-- Oracle SQL query
SELECT 1 as id, 'Oracle' as name FROM dual;
`,
};
